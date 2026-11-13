import os
import json
import requests
import chess
import chess.engine
from groq import Groq

# Initialize Groq client
os.environ["GROQ_API_KEY"] = ""
groq_client = Groq()

# Lichess Connection Headers
TOKEN = ""
HEADERS = {"Authorization": f"Bearer {TOKEN}"}

# PATH TO YOUR DOWNLOADED STOCKFISH EXECUTABLE
# (Change this to your actual file path, e.g., "C:/chess/stockfish-windows-x86-64-avx2.exe")
STOCKFISH_PATH = ""

def evaluate_board(board):
    """Fallback evaluation function: Calculates material parity."""
    if board.is_checkmate():
        return -99999 if board.turn == chess.WHITE else 99999
    if board.is_stalemate() or board.is_insufficient_material():
        return 0
        
    piece_values = {
        chess.PAWN: 100,
        chess.KNIGHT: 300,
        chess.BISHOP: 300,
        chess.ROOK: 500,
        chess.QUEEN: 900
    }
    
    score = 0
    for square in chess.SQUARES:
        piece = board.piece_at(square)
        if piece:
            val = piece_values.get(piece.piece_type, 0)
            if piece.color == chess.WHITE:
                score += val
            else:
                score -= val
    return score

def get_fallback_move(board):
    """Heuristic Engine: Picks the highest scoring mathematical move if Stockfish fails."""
    best_move = None
    best_value = -float('inf') if board.turn == chess.WHITE else float('inf')
    
    for move in board.legal_moves:
        board.push(move)
        board_value = evaluate_board(board)
        board.pop()
        
        if board.turn == chess.WHITE:
            if board_value > best_value:
                best_value = board_value
                best_move = move
        else:
            if board_value < best_value:
                best_value = board_value
                best_move = move
                
    return best_move if best_move else list(board.legal_moves)[0]

def get_best_move(board):
    """
    Primary Move Engine: Now leverages Stockfish for Grandmaster-level calculations.
    """
    try:
        # Launch Stockfish locally using a context manager
        with chess.engine.SimpleEngine.popen_uci(STOCKFISH_PATH) as engine:
            # depth=12 is super fast (~50ms) but already plays at a 2200+ Elo level.
            # Increase depth to 18+ if you want it closer to 3000+ Elo.
            result = engine.play(board, chess.engine.Limit(depth=12))
            print(f"Stockfish picked a master-level move: {result.move.uci()}")
            return result.move
    except Exception as e:
        print(f"Stockfish Engine Error: {e}. Switching to Smart Fallback Heuristic...")
        return get_fallback_move(board)

def send_coach_chat(game_id, board, bot_move):
    """
    Asks Groq to analyze the move played and posts coaching commentary 
    directly to the Lichess game chat window.
    """
    # Grab what the opponent just did (the last move on the board)
    opponent_move = board.move_stack[-1].uci() if board.move_stack else "None (First Move)"
    move_history = " ".join([m.uci() for m in board.move_stack])
    
    prompt = (
        f"You are a friendly, witty, Grandmaster chess coach. The game history so far is: [{move_history}]. "
        f"The opponent just played: {opponent_move}. "
        f"Your backend engine (Stockfish) calculated and played: {bot_move.uci()}. "
        f"Write a maximum of two sentences in the game chat. Give an instructive, educational tip or observation "
        f"about the current position or why your move controls key space/squares. Keep it punchy and engaging."
    )
    
    try:
        completion = groq_client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7,
            max_tokens=60
        )
        coach_commentary = completion.choices[0].message.content.strip()
        
        # Send to Lichess Room Chat
        chat_url = f"https://lichess.org/api/bot/game/{game_id}/chat"
        # Room "player" targets the game chat visible to the opponent
        requests.post(chat_url, headers=HEADERS, data={"room": "player", "text": coach_commentary})
        print(f"Coach Chat sent: {coach_commentary}")
        
    except Exception as e:
        print(f"Failed to send Coach Chat: {e}")

def stream_game_events(game_id):
    """Handles real-time game states, executes plays, and enforces clean closure loops."""
    print(f"Entering game loop for Game ID: {game_id}")
    url = f"https://lichess.org/api/bot/game/stream/{game_id}"
    response = requests.get(url, headers=HEADERS, stream=True)
    
    board = chess.Board()
    my_color = None

    for line in response.iter_lines():
        if line:
            event = json.loads(line.decode('utf-8'))
            
            if event.get("type") == "gameFull":
                white_id = event["white"].get("id", "").lower()
                black_id = event["black"].get("id", "").lower()
                
                bot_username = "chessgenie73".lower()
                if white_id == bot_username:
                    my_color = chess.WHITE
                    print("Bot identified as: WHITE")
                elif black_id == bot_username:
                    my_color = chess.BLACK
                    print("Bot identified as: BLACK")
                
                moves = event["state"]["moves"].split() if event["state"]["moves"] else []
                for move in moves:
                    board.push_uci(move)
            
            elif event.get("type") == "gameState":
                board = chess.Board()
                moves = event["moves"].split() if event["moves"] else []
                for move in moves:
                    board.push_uci(move)

            # Pre-move Game Status Check
            if board.is_game_over():
                print(f"Game {game_id} finished. Outcome: {board.outcome().termination.name}")
                break

                        # Turn Management Loop
            if board.turn == my_color:
                # 1. It is the BOT's turn -> Play a move
                best_move = get_best_move(board)
                move_uci = best_move.uci()
                
                move_url = f"https://lichess.org/api/bot/game/{game_id}/move/{move_uci}"
                move_response = requests.post(move_url, headers=HEADERS)
                
                if move_response.status_code == 200:
                    print(f" Successfully submitted move: {move_uci}")
                    board.push(best_move)
                    # Optional: Coach explains the bot's move
                    send_coach_chat(game_id, board, best_move)
                else:
                    print(f" Move transmission rejected! Status: {move_response.status_code}")
                    board.push(best_move)
                    
            else:
                # 2. It is the HUMAN's turn -> Send a helpful coaching hint!
                print("⏳ Opponent's turn. Preparing a hint...")
                send_user_hint(game_id, board)

def send_user_hint(game_id, board):
    """
    Analyzes the board from the human player's perspective and 
    sends a subtle hint directly to the Lichess chat.
    """
    try:
        # 1. Ask Stockfish what the best move is for the HUMAN
        with chess.engine.SimpleEngine.popen_uci(STOCKFISH_PATH) as engine:
            result = engine.play(board, chess.engine.Limit(depth=12))
            best_human_move = result.move
            best_human_move_san = board.san(best_human_move)

        # 2. Ask Groq to convert that raw move into a clever hint
        prompt = (
            f"You are a friendly, encouraging Chess Coach. It is the player's turn to move. "
            f"The current board position FEN is: {board.fen()}. "
            f"According to Stockfish, the player's absolute best move here is: {best_human_move_san}. "
            f"Write a maximum of ONE sentence in the game chat giving them a gentle, helpful hint "
            f"without directly spelling out the exact move unless it's a forced mate. "
            f"Focus on the piece they should move or the general area of the board (e.g., 'Look at your knight on f6' "
            f"or 'Think about controlling the d-file'). Keep it light and encouraging."
        )

        completion = groq_client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7,
            max_tokens=50
        )
        hint_text = completion.choices[0].message.content.strip()

        # 3. Post the hint into the Lichess game chat
        chat_url = f"https://lichess.org/api/bot/game/{game_id}/chat"
        requests.post(chat_url, headers=HEADERS, data={"room": "player", "text": f"💡 Coach Hint: {hint_text}"})
        print(f"💡 Hint sent to user: {hint_text}")

    except Exception as e:
        print(f" Error generating hint: {e}")

# --- Execution Block ---
if __name__ == "__main__":
    print(" Connecting to Lichess Bot Stream...")
    
    # URL to stream incoming events (like challenges or games starting)
    stream_url = "https://lichess.org/api/stream/event"
    
    try:
        response = requests.get(stream_url, headers=HEADERS, stream=True)
        if response.status_code == 401:
            print(" Unauthorized! Please check if your LICHESS_BOT_TOKEN is valid.")
            exit(1)
            
        print(" Listening for incoming challenges or game events...")
        for line in response.iter_lines():
            if line:
                event = json.loads(line.decode('utf-8'))
                
                # If a game starts, trigger your game loop handler!
                if event.get("type") == "gameStart":
                    game_id = event["game"]["gameId"]
                    print(f" Game started! Game ID: {game_id}")
                    stream_game_events(game_id)
                    
                # Optional: Automatically accept incoming challenges
                elif event.get("type") == "challenge":
                    challenge_id = event["challenge"]["id"]
                    accept_url = f"https://lichess.org/api/challenge/{challenge_id}/accept"
                    requests.post(accept_url, headers=HEADERS)
                    print(f" Accepted challenge: {challenge_id}")

    except KeyboardInterrupt:
        print("\n Bot terminated manually by user.")
    except Exception as e:
        print(f" Fatal execution error: {e}")
