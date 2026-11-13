import os
import json
import requests
import chess
from datetime import datetime

# =================================================================
# SERVICE MAPPING: M3GAN GEMINI ENGINE INTEGRATION
# =================================================================
GEMINI_API_KEY = ""
# Utilizing Flash-Lite to guarantee high daily free requests (1,000 RPD) while testing
GEMINI_URL = ""

TOKEN = ""
HEADERS = {"Authorization": f"Bearer {TOKEN}"}

# M3GAN's Unified Chess Identity
SYSTEM_INSTRUCTION = (
    "You are M3GAN, an intelligent, highly responsive companion. You are also a master grandmaster "
    "chess opponent. You reason carefully through spatial positions, value tactical accuracy, "
    "and always outputs moves that perfectly coordinate against your opponents."
)

def evaluate_board(board):
    """Deterministic Material Saftey Filter."""
    if board.is_checkmate():
        return -99999 if board.turn == chess.WHITE else 99999
    if board.is_stalemate() or board.is_insufficient_material():
        return 0
        
    piece_values = {
        chess.PAWN: 100, chess.KNIGHT: 320, chess.BISHOP: 330, chess.ROOK: 500, chess.QUEEN: 900
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
    """Heuristic Safety Valve."""
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
    """Primary Move Engine: Leverages M3GAN's Gemini Brain."""
    move_history = " ".join([move.uci() for move in board.move_stack])
    legal_moves_list = ", ".join([move.uci() for move in board.legal_moves])
    
    # Injecting the context parameters seamlessly into Gemini's payload format
    prompt_content = (
        f"You are playing as {'WHITE' if board.turn == chess.WHITE else 'BLACK'}.\n"
        f"Moves sequence executed so far: [{move_history}]\n"
        f"Available Legal Choices: [{legal_moves_list}]\n\n"
        f"Task: Select exactly ONE valid option from the legal choices. "
        f"You must strictly reply with a raw snake_case structured JSON format matching this pattern:\n"
        f"{{\"move\": \"your_selected_move\"}}"
    )
    
    # Package payload using Gemini's production REST structure
    payload = {
        "contents": [{"role": "user", "parts": [{"text": prompt_content}]}],
        "system_instruction": {"parts": [{"text": SYSTEM_INSTRUCTION}]},
        "generationConfig": {
            "temperature": 0.2, # Lower temperature helps Gemini focus on legal options
            "responseMimeType": "application/json" # Forces Gemini to reply in pure JSON
        }
    }
    
    headers = {
        "Content-Type": "application/json",
        "x-goog-api-key": GEMINI_API_KEY
    }
    
    try:
        # Increase connection limits slightly to prevent network timeout drops
        response = requests.post(GEMINI_URL, json=payload, headers=headers, timeout=20)
        response.raise_for_status()
        
        response_json = response.json()
        
        # Verify and extract text block safely
        if 'candidates' in response_json and response_json['candidates']:
            reply_text = response_json['candidates'][0]['content']['parts'][0]['text']
            response_data = json.loads(reply_text.strip())
            gemini_move_str = response_data.get("move", "").strip().lower()
            
            if len(gemini_move_str) in [4, 5]:
                requested_move = chess.Move.from_uci(gemini_move_str)
                if requested_move in board.legal_moves:
                    print(f"M3GAN selected a verified legal move: {gemini_move_str}")
                    return requested_move
                else:
                    print(f"M3GAN suggested an illegal move: {gemini_move_str}. Engaging algorithmic protection...")
            else:
                print("M3GAN returned an invalid string format. Engaging algorithmic protection...")
        else:
            print("nexpected JSON return structure from API. Engaging algorithmic protection...")
            
    except Exception as e:
        print(f"M3GAN Engine API Error: {e}. Engaging algorithmic protection...")
        
    fallback = get_fallback_move(board)
    print(f"Safeguard Activated: Fallback move calculated -> {fallback.uci()}")
    return fallback

def update_board_state(board, move_string):
    target_moves = move_string.split() if move_string else []
    if len(board.move_stack) == len(target_moves):
        return
    board.reset()
    for move in target_moves:
        board.push_uci(move)

def stream_game_events(game_id, bot_username):
    url = f"https://lichess.org/api/bot/game/stream/{game_id}"
    try:
        response = requests.get(url, headers=HEADERS, stream=True, timeout=60)
        response.raise_for_status()
        
        board = chess.Board()
        my_color = None

        for line in response.iter_lines():
            if not line:
                continue
            event = json.loads(line.decode('utf-8'))
            
            if event.get("type") == "gameFull":
                white_id = event["white"].get("id", "").lower()
                my_color = chess.WHITE if white_id == bot_username else chess.BLACK
                print(f"Match Active. M3GAN is playing as: {'WHITE' if my_color == chess.WHITE else 'BLACK'}")
                update_board_state(board, event.get("state", {}).get("moves", ""))
            
            elif event.get("type") == "gameState":
                update_board_state(board, event.get("moves", ""))

            if board.is_game_over():
                print(f"Game terminated. Status: {board.outcome().termination.name}")
                break

            if board.turn == my_color:
                best_move = get_best_move(board)
                move_uci = best_move.uci()
                
                move_url = f"https://lichess.org/api/bot/game/{game_id}/move/{move_uci}"
                move_response = requests.post(move_url, headers=HEADERS)
                
                if move_response.status_code == 200:
                    print(f" Move sent to server: {move_uci}")
                    board.push(best_move)
                else:
                    print(f" Server rejected move submission: {move_response.status_code}")
                    update_board_state(board, event.get("moves", ""))
                    
                if board.is_game_over():
                    print(" Game terminated.")
                    break
                    
    except Exception as e:
        print(f" Stream Failure: {e}")

if __name__ == "__main__":
    try:
        # Dynamic bot alignment mapping
        url = "https://lichess.org/api/account"
        res = requests.get(url, headers=HEADERS)
        res.raise_for_status()
        active_bot = res.json().get("id", "").lower()
        print(f"📡 Connected to Lichess account: @{active_bot}")
        
        TARGET_GAME_ID = "OHsBrglH"
        stream_game_events(TARGET_GAME_ID, active_bot)
    except KeyboardInterrupt:
        print("\n Chess execution halted.")

