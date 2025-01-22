import os
import asyncio
import aiohttp
import json
import html
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), '.env'))

class QuizGame:
    def __init__(self, chat_id):
        self.chat_id = chat_id
        self.players = {}
        self.current_question = None
        self.question_start_time = None
        self.is_active = False
        self.questions = []
        self.current_question_index = 0
        self.message_ids = []  # To track sent messages
        self.answered_players = set()
        self.correct_players = set()
        self.next_question_task = None
        self.api_key = os.getenv('QUIZ_API_KEY')
        self.auto_advance_task = None
        
        # Ensure data directory exists
        self.data_dir = os.path.join(os.path.dirname(__file__), 'Pydata')
        os.makedirs(self.data_dir, exist_ok=True)
        
        # Set up file paths
        self.players_file = os.path.join(self.data_dir, 'players.json')
        self.leaderboard_file = os.path.join(self.data_dir, 'leaderboard.json')
        self.load_data()

    def load_data(self):
        """Load player data and leaderboard from files"""
        try:
            if os.path.exists(self.players_file):
                with open(self.players_file, 'r') as f:
                    self.registered_players = json.load(f)
            else:
                self.registered_players = {}

            if os.path.exists(self.leaderboard_file):
                with open(self.leaderboard_file, 'r') as f:
                    self.leaderboard_data = json.load(f)
            else:
                self.leaderboard_data = {
                    'total_players': 0,
                    'online_players': 0,
                    'rankings': {}
                }
        except Exception as e:
            print(f"Error loading data: {e}")
            self.registered_players = {}
            self.leaderboard_data = {
                'total_players': 0,
                'online_players': 0,
                'rankings': {}
            }

    def register_player(self, user_id, username):
        """Register a new player or update existing player's username"""
        # Convert user_id to string since JSON only supports string keys
        user_id = str(user_id)
        
        if user_id not in self.registered_players:
            self.registered_players[user_id] = {
                'username': username,
                'score': 0
            }
        else:
            # Update username if changed
            self.registered_players[user_id]['username'] = username
        
        # Save updated player data
        self.save_data()
        return True  
    
    async def show_question(self, update_or_message):
        """Display the current question with options"""
        # Determine whether we received an Update or a Message
        if isinstance(update_or_message, Update):
            message = update_or_message.effective_message
        else:
            message = update_or_message  # Assume it's already a Message object

        # Cancel any existing timers
        if self.next_question_task and not self.next_question_task.done():
            self.next_question_task.cancel()
        if self.auto_advance_task and not self.auto_advance_task.done():
            self.auto_advance_task.cancel()

        if self.current_question_index >= len(self.questions):
            await self.end_game(message)
            return

        self.answered_players.clear()
        self.correct_players.clear()
        question = self.questions[self.current_question_index]
        self.current_question = question
        self.question_start_time = datetime.now()

        # Create buttons for valid answers
        options = []
        for key, value in question['answers'].items():
            if value:  # Only add non-null answers
                options.append(InlineKeyboardButton(value, callback_data=f"answer_{key}"))

        keyboard = [options[i:i + 2] for i in range(0, len(options), 2)]
        reply_markup = InlineKeyboardMarkup(keyboard)

        sent_message = await message.reply_text(
            f"❓ Question {self.current_question_index + 1}/{len(self.questions)}:\n\n"
            f"{question['question']}\n\n"
            f"⏱ You have 60 seconds to answer!",
            reply_markup=reply_markup
        )
        self.message_ids.append(sent_message.message_id)
        # Start the auto-advance timer
        self.auto_advance_task = asyncio.create_task(self.auto_advance_question(sent_message))

    async def delete_previous_messages(self, bot):
        """Delete all game-related messages"""
        for message_id in self.message_ids:
            try:
                await bot.delete_message(chat_id=self.chat_id, message_id=message_id)
            except Exception as e:
                print(f"Failed to delete message {message_id}: {e}")
        self.message_ids.clear()

    async def fetch_questions(self, category=None, limit=10):
        """Fetch questions from the quiz API"""
        if not self.api_key:
            print("Error: QUIZ_API_KEY not found in environment variables")
            return False
            
        base_url = f"https://quizapi.io/api/v1/questions?apiKey={self.api_key}&limit={limit}"
        if category:
            base_url += f"&category={category}"
        
        retries = 3
        for attempt in range(retries):
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(base_url) as response:
                        if response.status == 200:
                            questions_data = await response.json()
                            formatted_questions = []
                            
                            for q in questions_data:
                                formatted_q = {
                                    'question': q['question'],
                                    'answers': {
                                        'a': q['answers'].get('answer_a'),
                                        'b': q['answers'].get('answer_b'),
                                        'c': q['answers'].get('answer_c'),
                                        'd': q['answers'].get('answer_d')
                                    },
                                    'correct_answers': {
                                        'a_correct': q['correct_answers'].get('answer_a_correct'),
                                        'b_correct': q['correct_answers'].get('answer_b_correct'),
                                        'c_correct': q['correct_answers'].get('answer_c_correct'),
                                        'd_correct': q['correct_answers'].get('answer_d_correct')
                                    }
                                }
                                formatted_questions.append(formatted_q)
                            
                            self.questions = formatted_questions
                            return True
                        else:
                            print(f"API request failed with status {response.status}")
                            
            except Exception as e:
                print(f"Error fetching questions (attempt {attempt + 1}/{retries}): {e}")
                if attempt == retries - 1:
                    return False
                await asyncio.sleep(1)
        
        return False
    
    async def auto_advance_question(self, message):
        """Auto advance to the next question after 60 seconds"""
        try:
            await asyncio.sleep(60)  # Wait for 60 seconds
            if self.is_active:
                await message.reply_text("⏰ Time's up! Moving to the next question...")
                self.current_question_index += 1
                await self.show_question(message)
        except asyncio.CancelledError:
            # Handle cancellation gracefully
            pass

    async def end_game(self, message):
        """End the game and display final results"""
        self.is_active = False

        # Sort players by points
        sorted_players = sorted(
            self.players.items(),
            key=lambda x: (x[1]['points'], x[1]['correct_answers']),
            reverse=True
        )

        # Prepare results message
        results = "🎮 Game Over! Final Results:\n\n"
        for rank, (user_id, stats) in enumerate(sorted_players, 1):
            emoji = "🥇" if rank == 1 else "🥈" if rank == 2 else "🥉" if rank == 3 else "👤"
            results += (
                f"{emoji} {stats['name']}\n"
                f"Points: {stats['points']} | "
                f"Correct: {stats['correct_answers']} | "
                f"Speed Bonuses: {stats['fast_bonuses']}\n\n"
            )

        await message.reply_text(results)

        # Update leaderboard data
        for user_id, stats in self.players.items():
            str_user_id = str(user_id)
            if str_user_id not in self.leaderboard_data['rankings']:
                self.leaderboard_data['rankings'][str_user_id] = {
                    'username': stats['name'],
                    'total_points': 0,
                    'games_played': 0
                }

            self.leaderboard_data['rankings'][str_user_id]['total_points'] += stats['points']
            self.leaderboard_data['rankings'][str_user_id]['games_played'] += 1

        self.save_data()    

    def save_data(self):
        """Save player data and leaderboard to files"""
        try:
            with open(self.players_file + '.tmp', 'w') as f:
                json.dump(self.registered_players, f, indent=4)
            os.replace(self.players_file + '.tmp', self.players_file)

            with open(self.leaderboard_file + '.tmp', 'w') as f:
                json.dump(self.leaderboard_data, f, indent=4)
            os.replace(self.leaderboard_file + '.tmp', self.leaderboard_file)
            return True
        except Exception as e:
            print(f"Error saving data: {e}")
            return False

    

    def calculate_speed_bonus(self, answer_time):
        """Calculate bonus points based on answer speed"""
        seconds_elapsed = (answer_time - self.question_start_time).total_seconds()
        if seconds_elapsed <= 5:
            return 3  # Super fast bonus
        elif seconds_elapsed <= 15:
            return 2  # Fast bonus
        elif seconds_elapsed <= 30:
            return 1  # Quick bonus
        return 0  # No bonus





# Global games dictionary
games = {}

async def start_trivia(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Announce trivia game and rules"""
    chat_id = update.effective_chat.id
    
    # Create a new game instance in "announcement" state
    game = QuizGame(chat_id)
    game.is_active = False  # Game not active yet, but announced
    games[chat_id] = game
    
    rules_message = """
🎮 Welcome to the Speed Trivia Game! 🎮

Rules:
1️⃣ Use /join_trivia to participate in the game
2️⃣ Answer quickly for bonus points:
   • 3 points: Super fast (within 5 seconds)
   • 2 points: Fast (within 15 seconds)
   • 1 point: Quick (within 30 seconds)
3️⃣ Each player can answer only once per question
4️⃣ Questions automatically advance after 60 seconds
5️⃣ Check rankings with /leaderboard

Commands:
🎯 /join_trivia - Join the game
📊 /leaderboard - View rankings

Waiting for players to join...
Admin will start the game with /start_game when ready!
"""
    await update.message.reply_text(rules_message)



async def join_trivia(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Allow players to join the game"""
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    username = update.effective_user.username or update.effective_user.first_name

    if chat_id not in games:
        await update.message.reply_text("❌ No game has been announced! Wait for /start_trivia")
        return
        
    game = games[chat_id]
    
    if game.is_active:
        await update.message.reply_text("⚠️ Game is already in progress! Wait for the next game.")
        return

    if user_id in game.players:
        await update.message.reply_text("ℹ️ You're already registered for this game!")
        return

    # Register player
    game.register_player(user_id, username)
    game.players[user_id] = {
        'name': username,
        'points': 0,
        'correct_answers': 0,
        'fast_bonuses': 0
    }
    game.save_data()

    player_count = len(game.players)
    await update.message.reply_text(
        f"✅ Welcome {username}! You've joined the game.\n"
        f"Total players registered: {player_count}\n\n"
        "Remember:\n"
        "• Answer quickly for bonus points\n"
        "• You can only answer once per question\n"
        "Good luck! 🍀"
    )

async def is_admin(update: Update) -> bool:
    """Check if the user is an admin"""
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    chat_member = await update.effective_chat.get_member(user_id)
    return chat_member.status in ['administrator', 'creator']

async def start_game(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin command to start a new game"""
    if not await is_admin(update):
        await update.message.reply_text("❌ This command is only available to administrators.")
        return

    chat_id = update.effective_chat.id
    
    if chat_id not in games:
        await update.message.reply_text("❌ Please use /start_trivia to announce the game first!")
        return
        
    chat_id = update.effective_chat.id
    
    if chat_id not in games:
        await update.message.reply_text("❌ Please use /start_trivia to announce the game first!")
        return
        
    game = games[chat_id]
    
    if game.is_active:
        await update.message.reply_text("❌ A game is already in progress!")
        return

    if len(game.players) == 0:
        await update.message.reply_text("❌ No players have joined yet! Wait for players to use /join_trivia")
        return

    # Delete old game-related messages
    await game.delete_previous_messages(context.bot)

    await update.message.reply_text("🎮 Starting trivia game...")
    
    if await game.fetch_questions():
        game.is_active = True  # Now we activate the game
        
        await update.message.reply_text(
            f"🎯 Game is starting with {len(game.players)} players!\n\n"
            "⏱ You have 60 seconds per question\n"
            "🎁 Remember the bonus points:\n"
            "   • Within 5s: +3 points\n"
            "   • Within 15s: +2 points\n"
            "   • Within 30s: +1 point\n\n"
            "First question coming up in 10 seconds..."
        )
        await asyncio.sleep(10)  # Give players time to prepare
        await game.show_question(update.effective_message)
    else:
        game.is_active = False  # Reset if question fetch fails
        await update.message.reply_text("Failed to fetch questions. Please try again later.")


async def next_game(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin command to move to next question"""
    if not await is_admin(update):
        await update.message.reply_text("❌ This command is only available to administrators.")
        return

    chat_id = update.effective_chat.id
    
    if chat_id not in games or not games[chat_id].is_active:
        await update.message.reply_text("❌ No active game!")
        return

    game = games[chat_id]
    
    # Cancel any existing auto-progression task
    if game.next_question_task and not game.next_question_task.done():
        game.next_question_task.cancel()
    
    # Show answer statistics before moving to next question
    correct_players = sum(1 for player_id in game.answered_players 
                         if player_id in game.players)
    total_players = len(game.players)
    
    await update.message.reply_text(
        f"📊 Question Summary:\n"
        f"Players answered: {correct_players}/{total_players}\n"
        "Moving to next question..."
    )
    
    game.current_question_index += 1
    await game.show_question(update)

async def show_leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show current game and all-time leaderboard"""
    chat_id = update.effective_chat.id
    
    if chat_id not in games:
        await update.message.reply_text("❌ No game history in this chat!")
        return

    game = games[chat_id]
    
    # Current game leaderboard
    if game.is_active and game.players:
        current_text = "📊 Current Game Leaderboard:\n\n"
        sorted_current = sorted(
            game.players.items(),
            key=lambda x: (x[1]['points'], x[1]['fast_bonuses']),
            reverse=True
        )
        
        for rank, (user_id, player) in enumerate(sorted_current, 1):
            emoji = "🥇" if rank == 1 else "🥈" if rank == 2 else "🥉" if rank == 3 else "👤"
            current_text += (
                f"{emoji} {player['name']}\n"
                f"   Points: {player['points']} | "
                f"Correct: {player['correct_answers']} | "
                f"Speed Bonuses: {player['fast_bonuses']}\n"
            )
        await update.message.reply_text(current_text)
    
    # All-time leaderboard
    if game.leaderboard_data['rankings']:
        all_time_text = f"🏆 All-Time Leaderboard\n\n"
        all_time_text += f"Total Registered Players: {game.leaderboard_data['total_players']}\n"
        all_time_text += f"Currently Playing: {len(game.players)}\n\n"
        
        sorted_all_time = sorted(
            game.leaderboard_data['rankings'].items(),
            key=lambda x: x[1]['total_points'],
            reverse=True
        )[:10]  # Top 10 players
        
        for rank, (user_id, stats) in enumerate(sorted_all_time, 1):
            emoji = "🥇" if rank == 1 else "🥈" if rank == 2 else "🥉" if rank == 3 else f"{rank}."
            all_time_text += (
                f"{emoji} {stats['username']}\n"
                f"   Total Points: {stats['total_points']} | "
                f"Games: {stats['games_played']}\n"
            )
        await update.message.reply_text(all_time_text)


async def handle_answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle answer button callbacks"""
    query = update.callback_query
    chat_id = query.message.chat_id
    user_id = query.from_user.id
    username = query.from_user.username or query.from_user.first_name

    if chat_id not in games or not games[chat_id].is_active:
        await query.answer("❌ No active game!")
        return

    game = games[chat_id]
    
    # Check if player has joined the game
    if user_id not in game.players:
        await query.answer("❌ You must /join_trivia first!")
        return

    # Check if player already answered correctly
    if user_id in game.correct_players:
        await query.answer("✅ You already got this question correct!")
        return

    # Check if player already attempted this question
    if user_id in game.answered_players:
        await query.answer("❌ You already attempted this question!")
        return

    # Mark player as attempted
    game.answered_players.add(user_id)
    
    answer_time = datetime.now()
    answer_key = query.data.replace("answer_", "")
    correct = game.current_question['correct_answers'][f"{answer_key}_correct"] == "true"

    if correct:
        # Mark player as correct for this question
        game.correct_players.add(user_id)
        
        # Calculate speed bonus
        bonus = game.calculate_speed_bonus(answer_time)
        base_points = 1
        total_points = base_points + bonus
        game.players[user_id]['points'] += total_points
        game.players[user_id]['correct_answers'] += 1
        if bonus > 0:
            game.players[user_id]['fast_bonuses'] += 1

        # Create congratulation message
        bonus_emoji = "🚀" if bonus == 3 else "⚡" if bonus == 2 else "💨" if bonus == 1 else "✅"
        message = f"{bonus_emoji} Correct! "
        if bonus > 0:
            time_taken = (answer_time - game.question_start_time).total_seconds()
            message += f"Speed bonus: +{bonus} points! ({time_taken:.1f}s)"
        
        await query.answer(message)
        
        # Broadcast quick answers to chat
        if bonus >= 2:  # For super fast and fast answers
            await query.message.reply_text(
                f"🏃‍♂️ Amazing speed by {username}!\n"
                f"Time: {(answer_time - game.question_start_time).total_seconds():.1f}s\n"
                f"Points earned: {total_points}"
            )

        # If all players have answered correctly, move to next question immediately
        if len(game.correct_players) == len(game.players):
            if game.auto_advance_task and not game.auto_advance_task.done():
                game.auto_advance_task.cancel()
            await asyncio.sleep(2)  # Short delay to show results
            await query.message.reply_text("🎯 Everyone answered correctly! Moving to next question...")
            game.current_question_index += 1
            await game.show_question(query.message)
    else:
        await query.answer("❌ Wrong answer!")

    game.save_data()

    # Check if everyone has either answered correctly or attempted
    remaining_players = set(game.players.keys()) - game.answered_players
    if not remaining_players or len(game.correct_players) == len(game.players):
        if game.next_question_task and not game.next_question_task.done():
            game.next_question_task.cancel()
        await asyncio.sleep(2)  # Short delay to show results
        game.current_question_index += 1
        await game.show_question(query.message)

def main():
    """Main function to start the bot"""
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    application = Application.builder().token(token).build()

    # Add command handlers
    application.add_handler(CommandHandler("start_trivia", start_trivia))
    application.add_handler(CommandHandler("join_trivia", join_trivia))
    application.add_handler(CommandHandler("start_game", start_game))
    application.add_handler(CommandHandler("next_game", next_game))
    application.add_handler(CommandHandler("leaderboard", show_leaderboard))
    application.add_handler(CallbackQueryHandler(handle_answer, pattern="^answer_"))

    # Start the bot
    application.run_polling()

if __name__ == '__main__':
    main()