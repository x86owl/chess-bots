import time
import json
import requests
import chess
import os
from groq import Groq

# Initialize the Groq client (Make sure your API key is in your environment variables)
os.environ["GROQ_API_KEY"] = ""
groq_client = Groq()

# CONFIGURATION
TOKEN = ""  # Replace with your actual Lichess token
HEADERS = {"Authorization": f"Bearer {TOKEN}"}
LICHESS_URL = ""

def evaluate_board(board):
    """
    A simple AI evaluation function. 
    It counts piece values: Pawn=100, Knight/Bishop=300, Rook=500, Queen=900.
    Positive scores favor White; negative favor Black.
    """
    if board.is_checkmate():
        return -99999 if board.turn == chess.WHITE else 99999
    if board.is_stalemate() or board.is_insufficient_material():
        return 0

    # Piece point mappings
    piece_values = {
        chess.PAWN: 100, chess.KNIGHT: 300, chess.BISHOP: 330, 
        chess.ROOK: 500, chess.QUEEN: 900, chess.KING: 0
    }
    
    score = 0
    for square in chess.SQUARES:
        piece = board.piece_at(square)
        if piece:
            val = piece_values[piece.piece_type]
            if piece.color == chess.WHITE:
                score += val
            else:
                score -= val
    return score


def get_best_move(board):
    """
    Asks Groq to analyze the game state via PGN / UCI move list 
    and returns a validated legal move using strict JSON formatting.
    """
    move_history = " ".join([move.uci() for move in board.move_stack])
    legal_moves_list = ", ".join([move.uci() for move in board.legal_moves])
    
    # Tighten the prompt instructions
    prompt = (
        f"You are a professional chess engine. The moves played so far: [{move_history}]. "
        f"It is your turn. Select exactly ONE move from this list of legal moves: [{legal_moves_list}]. "
        f"You must reply with a valid JSON object matching this schema: {{\"move\": \"your_selected_move\"}}"
    )
    
    try:
        # Use the working model string we fixed earlier
        completion = groq_client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=20,
            # FORCE JSON output at the API gateway level
            response_format={"type": "json_object"} 
        )
        
        # Parse the forced JSON response safely
        response_data = json.loads(completion.choices[0].message.content.strip())
        groq_move_str = response_data.get("move", "").strip().lower()
        
        print(f" Groq picked: {groq_move_str}")
        
        # Verify it has the correct UCI layout before translating it
        if len(groq_move_str) in [4, 5]:
            requested_move = chess.Move.from_uci(groq_move_str)
            if requested_move in board.legal_moves:
                return requested_move
            else:
                print(f" Groq picked an ILLEGAL move ({groq_move_str}). Using fallback...")
        else:
            print(f" Groq returned an invalid string format. Using fallback...")
            
    except Exception as e:
        print(f" Parsing engine error: {e}")
    
    # Fallback to a legal move so the bot never stalls or forfeits
    return list(board.legal_moves)[0]


def stream_game_events(game_id):
    """Listens to the active live game state updates and submits moves."""
    print(f"Entering game loop for Game ID: {game_id}")
    
    url = f"https://lichess.org/api/bot/game/stream/{game_id}"
    response = requests.get(url, headers=HEADERS, stream=True)
    
    board = chess.Board()
    my_color = None

    for line in response.iter_lines():
        if line:
            event = json.loads(line.decode('utf-8'))
            
            # 1. Initial game configuration state
            if event.get("type") == "gameFull":
                white_id = event["white"].get("id", "").lower()
                black_id = event["black"].get("id", "").lower()
                print(f"Game loaded. White: {white_id} | Black: {black_id}")
                
                # Make sure this string exactly matches your bot's Lichess username
                bot_username = "ChessGenie73".lower() 
                
                if white_id == bot_username:
                    my_color = chess.WHITE
                    print("Bot identified as: WHITE")
                elif black_id == bot_username:
                    my_color = chess.BLACK
                    print("Bot identified as: BLACK")
                else:
                    print("Warning: Bot username did not match White or Black!")
                    
                moves = event["state"]["moves"].split() if event["state"]["moves"] else []
                for move in moves:
                    board.push_uci(move)
            
            # 2. Live state update when ANY player makes a move
            elif event.get("type") == "gameState":
                board = chess.Board()  # clear board state
                moves = event["moves"].split() if event["moves"] else []
                for move in moves:
                    board.push_uci(move)

            # 3. If it's our turn, calculate and send the move
            if board.turn == my_color and not board.is_game_over():
                best_move = get_best_move(board)
                move_uci = best_move.uci()
                
                # The correct, live move execution path
                move_url = f"https://lichess.org/api/bot/game/{game_id}/move/{move_uci}"
                
                # Execute the post request and save it immediately into move_response
                move_response = requests.post(move_url, headers=HEADERS)
                
                if move_response.status_code == 200:
                    print(f"Bot successfully played move: {move_uci}")
                else:
                    print(f"Server rejected move! Status: {move_response.status_code}")
                    print(f"Reason: {move_response.text}")


def monitor_incoming_challenges():
    """Main event loop monitoring incoming game requests."""
    print("Bot is online and listening for challenges...")
    url = "https://lichess.org/api/stream/event"

    response = requests.get(url, headers=HEADERS, stream=True)

    if response.status_code != 200:
        print(f"Error connecting to Lichess. Status Code: {response.status_code}")
        return

    for line in response.iter_lines():
        if line:
            try:
                event = json.loads(line.decode('utf-8'))
            except json.JSONDecodeError:
                continue
            
            # Auto-accept incoming challenges
            if event.get("type") == "challenge":
                challenge_id = event["challenge"]["id"]
                accept_url = f"https://lichess.org/api/challenge/{challenge_id}/accept"
                accecpt_rsponse = requests.post(accept_url, headers=HEADERS)
                if accecpt_rsponse.status_code == 200:
                    print(f"Accepted challenge {challenge_id}")
                else:
                    print(f"Failed to accept challenge. Status Code: {accecpt_rsponse.status_code}")
                    print(f"Reason: {accecpt_rsponse.text}")
                
            # Triggered when a match successfully initiates
            elif event.get("type") == "gameStart":
                game_id = event["game"]["id"]
                try:
                    stream_game_events(game_id)
                except Exception as e:
                    print(f"Game ended or error occurred: {e}")

if __name__ == "__main__":
    monitor_incoming_challenges()