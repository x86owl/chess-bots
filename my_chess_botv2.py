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
    """Counts piece values to evaluate the board state if Groq fails."""
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
    """Calculates a smart backup move using minimax/greedy math if Groq hallucinates."""
    best_move = None
    # White wants to maximize score, Black wants to minimize score
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
    """Asks Groq for a move via JSON format, falls back to greedy math if illegal."""
    move_history = " ".join([move.uci() for move in board.move_stack])
    legal_moves_list = ", ".join([move.uci() for move in board.legal_moves])
    
    prompt = (
        f"You are a professional chess engine playing as {'WHITE' if board.turn == chess.WHITE else 'BLACK'}. "
        f"The moves played so far: [{move_history}]. "
        f"Select exactly ONE move from this list of legal moves: [{legal_moves_list}]. "
        f"You must reply with a valid JSON object matching this schema: {{\"move\": \"your_selected_move\"}}"
    )
    
    try:
        completion = groq_client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=20,
            response_format={"type": "json_object"}
        )
        
        response_data = json.loads(completion.choices[0].message.content.strip())
        groq_move_str = response_data.get("move", "").strip().lower()
        
        if len(groq_move_str) in [4, 5]:
            requested_move = chess.Move.from_uci(groq_move_str)
            if requested_move in board.legal_moves:
                print(f"Groq picked a legal move: {groq_move_str}")
                return requested_move
            else:
                print(f"Groq picked an ILLEGAL move ({groq_move_str}). Switching to Smart Fallback AI...")
        else:
            print("Groq returned invalid text formatting. Switching to Smart Fallback AI...")
            
    except Exception as e:
        print(f"Groq Engine Error: {e}. Switching to Smart Fallback AI...")
    
    # Smart mathematical backup instead of random movements
    fallback = get_fallback_move(board)
    print(f"Smart Fallback AI calculated move: {fallback.uci()}")
    return fallback


def stream_game_events(game_id):
    """Listens to active live game state updates, handles turns, and exits cleanly on game over."""
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
                
                # Double-check username handling match
                bot_username = "ChessGenie73".lower()
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

            # Check for Checkmate / Draw BEFORE attempting to make a move
            if board.is_game_over():
                print(f"Game {game_id} has concluded! Reason: {board.outcome().termination.name}")
                print("Returning to global matchmaking listener...")
                break

            # Trigger bot calculation if it's our turn
            if board.turn == my_color:
                best_move = get_best_move(board)
                move_uci = best_move.uci()
                
                move_url = f"https://lichess.org/api/bot/game/{game_id}/move/{move_uci}"
                move_response = requests.post(move_url, headers=HEADERS)
                
                if move_response.status_code == 200:
                    print(f"Successfully submitted move: {move_uci}")
                else:
                    print(f"Move transmission rejected! Status: {move_response.status_code}")
                    
                # Push locally to check if the bot's own move ended the game immediately
                board.push(best_move)
                if board.is_game_over():
                    print(f"Game {game_id} has concluded! Reason: {board.outcome().termination.name}")
                    print("Returning to global matchmaking listener...")
                    break

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