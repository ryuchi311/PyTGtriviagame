import os
import asyncio
import aiohttp
import json
import html
import random
import google.generativeai as genai
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), '.env'))

# Configure Gemini AI
genai.configure(api_key=os.getenv('GEMINI_API_KEY'))
model = genai.GenerativeModel('gemini-1.5-flash')


class QuizGame:
    def __init__(self, chat_id):
        self.chat_id = chat_id
        self.players = {}
        self.current_question = None
        self.question_start_time = None
        self.is_active = False
        self.questions = []
        self.current_question_index = 0
        self.message_ids = []
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
        
        # Initialize default data structures
        self.registered_players = {}
        self.leaderboard_data = {
            'total_players': 0,
            'online_players': 0,
            'rankings': {}
        }
        
        # Load existing data if available
        self.load_data()


    def load_data(self):
        """Load player data and leaderboard from files"""
        try:
            if os.path.exists(self.players_file):
                with open(self.players_file, 'r') as f:
                    loaded_players = json.load(f)
                    if isinstance(loaded_players, dict):
                        self.registered_players = loaded_players

            if os.path.exists(self.leaderboard_file):
                with open(self.leaderboard_file, 'r') as f:
                    loaded_leaderboard = json.load(f)
                    if isinstance(loaded_leaderboard, dict):
                        # Ensure all required keys exist
                        self.leaderboard_data.update({
                            'total_players': loaded_leaderboard.get('total_players', 0),
                            'online_players': loaded_leaderboard.get('online_players', 0),
                            'rankings': loaded_leaderboard.get('rankings', {})
                        })
        except Exception as e:
            print(f"Error loading data: {e}")
            # Keep using the default initialized data structures if loading fails
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
        try:
            if isinstance(update_or_message, Update):
                message = update_or_message.effective_message
            else:
                message = update_or_message

            # Check if we have questions
            if not self.questions:
                await message.reply_text("❌ No questions available! Please try starting a new game.")
                return

            # Check if we've reached the end
            if self.current_question_index >= len(self.questions):
                await self.end_game(message)
                return

            # Cancel existing timer if any
            if self.auto_advance_task and not self.auto_advance_task.done():
                self.auto_advance_task.cancel()

            # Clear previous question data
            self.answered_players.clear()
            self.correct_players.clear()
            
            # Get current question
            question = self.questions[self.current_question_index]
            self.current_question = question
            self.question_start_time = datetime.now()

            # Create keyboard with randomized answers
            keyboard = []
            answer_items = list(question['answers'].items())
            random.shuffle(answer_items)
            for key, value in answer_items:
                keyboard.append([InlineKeyboardButton(value, callback_data=f"answer_{key}")])
            reply_markup = InlineKeyboardMarkup(keyboard)

            # Send question with join reminder
            total_players = len(self.players)
            question_text = (
                f"❓ Question {self.current_question_index + 1}/{len(self.questions)}\n\n"
                f"Category: {question['category']}\n\n"
                f"{question['question']}\n\n"
                f"⏱ Time: 60 seconds to answer!\n\n"
                f"👥 Current players: {total_players}\n"
                "New players can join anytime with /join_trivia"
            )
            
            sent_message = await message.reply_text(question_text, reply_markup=reply_markup)
            self.message_ids.append(sent_message.message_id)
            
            # Start new timer
            self.auto_advance_task = asyncio.create_task(self.auto_advance_question(sent_message))
            
        except Exception as e:
            print(f"Error in show_question: {e}")
            await message.reply_text("❌ An error occurred while displaying the question. Please try again.")

    async def delete_previous_messages(self, bot):
        """Delete all game-related messages"""
        for message_id in self.message_ids:
            try:
                await bot.delete_message(chat_id=self.chat_id, message_id=message_id)
            except Exception as e:
                print(f"Failed to delete message {message_id}: {e}")
        self.message_ids.clear()


    async def get_answer_explanation(self, question, correct_answer):
        """Get explanation from Gemini AI about the answer"""
        try:
            prompt = f"""
            Question: {question}
            Correct Answer: {correct_answer}
            
            Please provide a funny humor brief, educational explanation (1-2 sentences) about why this is the correct answer.
            Keep the explanation concise but informative.
            """
            
            response = await asyncio.to_thread(
                model.generate_content,
                prompt
            )
            
            return response.text
        except Exception as e:
            print(f"Error getting Gemini explanation: {e}")
            return None

    async def fetch_questions(self, category=None, limit=100):
        """Fetch and format questions from OpenTDB"""
        category_mapping = {
            'general': 9,
            'books': 10,
            'film': 11,
            'music': 12,
            'science': 17,
            'computers': 18,
            'sports': 21,
            'history': 23,
            'geography': 22
        }

        base_url = "https://opentdb.com/api.php"
        params = {
            'amount': limit,
            'type': 'multiple'
        }
        
        if category and category.lower() in category_mapping:
            params['category'] = category_mapping[category.lower()]

        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(base_url, params=params) as response:
                    if response.status == 200:
                        data = await response.json()
                        if data['response_code'] == 0:
                            formatted_questions = []
                            for q in data['results']:
                                # Create answers dictionary
                                answers = {
                                    'a': html.unescape(q['correct_answer']),
                                    'b': html.unescape(q['incorrect_answers'][0]),
                                    'c': html.unescape(q['incorrect_answers'][1]),
                                    'd': html.unescape(q['incorrect_answers'][2])
                                }
                                
                                # Create correct_answers dictionary
                                correct_answers = {
                                    'a_correct': 'true',
                                    'b_correct': 'false',
                                    'c_correct': 'false',
                                    'd_correct': 'false'
                                }
                                
                                question = {
                                    'question': html.unescape(q['question']),
                                    'answers': answers,
                                    'correct_answers': correct_answers,
                                    'category': q['category']
                                }
                                formatted_questions.append(question)
                            
                            self.questions = formatted_questions
                            return True
                        
            except Exception as e:
                print(f"Error fetching questions: {str(e)}")
                return False
        return False

    
    async def auto_advance_question(self, message):
        """Wait exactly 60 seconds before advancing to next question"""
        try:
            # Wait full 60 seconds
            await asyncio.sleep(60)
            
            if self.is_active:
                # Get current question details
                question = self.questions[self.current_question_index]
                correct_answer = None
                for key, value in question['answers'].items():
                    if question['correct_answers'][f"{key}_correct"] == "true":
                        correct_answer = value
                        break

                # Get explanation from Gemini
                explanation = await self.get_answer_explanation(
                    question['question'],
                    correct_answer
                )

                # Show question summary with explanation
                correct_count = len(self.correct_players)
                total_players = len(self.players)
                
                summary = (
                    f"⏰ Time's up!\n\n"
                    f"📊 Question Summary:\n"
                    f"✅ Correct answers: {correct_count}/{total_players}\n"
                    f"👥 Players who answered: {len(self.answered_players)}/{total_players}\n\n"
                    f"✨ Correct Answer: {correct_answer}\n"
                )

                if explanation:
                    summary += f"\n🤖 Explanation:\n{explanation}\n"

                summary += "\nNext question in 30 seconds..."
                
                await message.reply_text(summary)
                
                # Create a separate task for showing the next question after delay
                async def show_next_question():
                    await asyncio.sleep(30)
                    if self.is_active:
                        self.current_question_index += 1
                        await self.show_question(message)
                
                # Schedule the next question
                asyncio.create_task(show_next_question())
                
        except asyncio.CancelledError:
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

        # Generate All-Time Hall of Fame
        all_time_text = "🏆 ALL-TIME HALL OF FAME 🏆\n" + "═" * 35 + "\n\n"
        
        # Add global statistics
        all_time_text += "📊 Global Statistics:\n"
        all_time_text += f"• Total Players: {self.leaderboard_data['total_players']}\n"
        all_time_text += f"• Active Players: {len(self.players)}\n"
        all_time_text += "═" * 35 + "\n\n"
        
        # Sort players by total points and filter out zero points
        sorted_all_time = sorted(
            [
                (user_id, stats) for user_id, stats in self.leaderboard_data['rankings'].items()
                if stats['total_points'] > 0  # Only include players with points > 0
            ],
            key=lambda x: x[1]['total_points'],
            reverse=True
        )[:10]  # Top 10 players
        
        all_time_text += "👑 Top 10 All-Time Champions:\n\n"
        for rank, (user_id, stats) in enumerate(sorted_all_time, 1):
            # Determine rank formatting
            if rank == 1:
                emoji = "👑 GRAND CHAMPION"
                separator = "═" * 25
            elif rank == 2:
                emoji = "🏅 LEGEND"
                separator = "─" * 25
            elif rank == 3:
                emoji = "🎖️ MASTER"
                separator = "─" * 25
            else:
                emoji = f"#{rank}"
                separator = "·" * 25
            
            # Calculate average points per game
            avg_points = stats['total_points'] / stats['games_played'] if stats['games_played'] > 0 else 0
            
            # Add player stats
            all_time_text += f"{emoji}\n{separator}\n"
            all_time_text += f"🎯 {stats['username']}\n"
            all_time_text += f"💫 Total Points: {stats['total_points']}\n"
            all_time_text += f"🎮 Games Played: {stats['games_played']}\n"
            all_time_text += f"📊 Avg Points/Game: {avg_points:.1f}\n\n"

        await message.reply_text(all_time_text)
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
    """Allow players to join the game at any time"""
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    username = update.effective_user.username or update.effective_user.first_name

    if chat_id not in games:
        await update.message.reply_text("❌ No game has been announced! Wait for /start_trivia")
        return
        
    game = games[chat_id]
    
    if user_id in game.players:
        await update.message.reply_text("ℹ️ You're already registered for this game!")
        return

    # Register player
    game.register_player(user_id, username)
    
    # Initialize player stats
    game.players[user_id] = {
        'name': username,
        'points': 0,
        'correct_answers': 0,
        'fast_bonuses': 0
    }
    
    game.save_data()

    # Different messages based on when they join
    if game.is_active:
        current_question = game.current_question_index + 1
        total_questions = len(game.questions)
        remaining_questions = total_questions - current_question

        await update.message.reply_text(
            f"👋 Welcome {username}! You've joined the game in progress!\n\n"
            f"📊 Current status:\n"
            f"• Question: {current_question}/{total_questions}\n"
            f"• Remaining questions: {remaining_questions}\n\n"
            "⚡ Speed bonus points:\n"
            "• Within 5s: +3 points\n"
            "• Within 15s: +2 points\n"
            "• Within 30s: +1 point\n\n"
            "Good luck! 🍀"
        )
    else:
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
    """Show current game and all-time leaderboard with enhanced visuals"""
    chat_id = update.effective_chat.id
    
    if chat_id not in games:
        await update.message.reply_text("❌ No game history in this chat!")
        return

    game = games[chat_id]
    
    # Current game leaderboard
    if game.is_active and game.players:
        current_text = "🎮 CURRENT GAME STANDINGS 🎮\n" + "═" * 35 + "\n\n"
        
        # Sort players by points, then by fast bonuses, then by correct answers
        sorted_current = sorted(
            game.players.items(),
            key=lambda x: (-x[1]['points'], -x[1]['fast_bonuses'], -x[1]['correct_answers'])
        )
        
        # Add game statistics
        total_answers = sum(1 for _ in game.answered_players)
        total_correct = len(game.correct_players)
        accuracy = (total_correct / total_answers * 100) if total_answers > 0 else 0
        
        current_text += f"📊 Game Statistics:\n"
        current_text += f"• Questions Completed: {game.current_question_index}/{len(game.questions)}\n"
        current_text += f"• Total Players: {len(game.players)}\n"
        current_text += f"• Overall Accuracy: {accuracy:.1f}%\n"
        current_text += "═" * 35 + "\n\n"
        
        # Player Rankings
        current_text += "👑 Player Rankings:\n\n"
        for rank, (user_id, player) in enumerate(sorted_current, 1):
            # Determine rank emoji and formatting
            if rank == 1:
                emoji = "🥇 LEADER"
                separator = "═" * 20
            elif rank == 2:
                emoji = "🥈 SECOND"
                separator = "─" * 20
            elif rank == 3:
                emoji = "🥉 THIRD"
                separator = "─" * 20
            else:
                emoji = f"{rank}th"
                separator = "·" * 20
                
            # Calculate player accuracy
            player_accuracy = (player['correct_answers'] / (game.current_question_index + 1) * 100) if game.current_question_index >= 0 else 0
            
            # Add player stats
            current_text += f"{emoji}\n{separator}\n"
            current_text += f"🎯 {player['name']}\n"
            current_text += f"📍 Score: {player['points']} points\n"
            current_text += f"✅ Correct: {player['correct_answers']}/{game.current_question_index + 1} ({player_accuracy:.1f}%)\n"
            current_text += f"⚡ Speed Bonuses: {player['fast_bonuses']}\n\n"
        
        await update.message.reply_text(current_text)
    
    # All-time leaderboard
    if game.leaderboard_data['rankings']:
        all_time_text = "🏆 ALL-TIME HALL OF FAME 🏆\n" + "═" * 35 + "\n\n"
        
        # Add global statistics
        all_time_text += "📊 Global Statistics:\n"
        all_time_text += f"• Total Players: {game.leaderboard_data['total_players']}\n"
        all_time_text += f"• Active Players: {len(game.players)}\n"
        all_time_text += "═" * 35 + "\n\n"
        
        # Sort players by total points
        sorted_all_time = sorted(
            game.leaderboard_data['rankings'].items(),
            key=lambda x: x[1]['total_points'],
            reverse=True
        )[:10]  # Top 10 players
        
        all_time_text += "👑 Top 10 All-Time Champions:\n\n"
        for rank, (user_id, stats) in enumerate(sorted_all_time, 1):
            # Determine rank formatting
            if rank == 1:
                emoji = "👑 GRAND CHAMPION"
                separator = "═" * 25
            elif rank == 2:
                emoji = "🏅 LEGEND"
                separator = "─" * 25
            elif rank == 3:
                emoji = "🎖️ MASTER"
                separator = "─" * 25
            else:
                emoji = f"#{rank}"
                separator = "·" * 25
            
            # Calculate average points per game
            avg_points = stats['total_points'] / stats['games_played'] if stats['games_played'] > 0 else 0
            
            # Add player stats with more details
            all_time_text += f"{emoji}\n{separator}\n"
            all_time_text += f"🎯 {stats['username']}\n"
            all_time_text += f"💫 Total Points: {stats['total_points']}\n"
            all_time_text += f"🎮 Games Played: {stats['games_played']}\n"
            all_time_text += f"📊 Avg Points/Game: {avg_points:.1f}\n\n"
        
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

        # Check if player already answered
        if user_id in game.answered_players:
            await query.answer("✅ You already answered this question!")
            return

        # Mark player as attempted
        game.answered_players.add(user_id)
        
        answer_time = datetime.now()
        answer_key = query.data.replace("answer_", "")
        correct = game.current_question['correct_answers'][f"{answer_key}_correct"] == "true"

        if correct:
            # Mark player as correct
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
            time_taken = (answer_time - game.question_start_time).total_seconds()
            message = f"{bonus_emoji} Correct! ({time_taken:.1f}s)"
            if bonus > 0:
                message += f"\nSpeed bonus: +{bonus} points!"
            
            await query.answer(message)
            
            # Broadcast quick answers to chat
            if bonus >= 2:
                await query.message.reply_text(
                    f"🏃‍♂️ Amazing speed by {username}!\n"
                    f"Time: {time_taken:.1f}s\n"
                    f"Points earned: {total_points}"
                )
        else:
            await query.answer("❌ Wrong answer!")

        game.save_data()
        
        # Don't auto-advance, let the timer complete

async def reset_scores(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Reset all player scores in the trivia game"""
    chat_id = update.effective_chat.id
    
    # Check if there's an existing game
    if chat_id not in games:
        await update.message.reply_text("❌ No game exists in this chat!")
        return
        
    # Get existing game instance
    game = games[chat_id]
    
    # Reset the game data
    game.players = {}
    game.registered_players = {}
    game.leaderboard_data = {
        'total_players': 0,
        'online_players': 0,
        'rankings': {}
    }
    
    # Save the reset data
    game.save_data()
    
    await update.message.reply_text("🔄 All player scores and game data have been reset!")

async def force_end_game(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin command to force end the current game and show final results"""
    if not await is_admin(update):
        await update.message.reply_text("❌ This command is only available to administrators.")
        return

    chat_id = update.effective_chat.id
    
    if chat_id not in games:
        await update.message.reply_text("❌ No game exists in this chat!")
        return
        
    game = games[chat_id]
    
    if not game.is_active:
        await update.message.reply_text("❌ No active game to end!")
        return

    # Cancel any existing timers
    if game.auto_advance_task and not game.auto_advance_task.done():
        game.auto_advance_task.cancel()
    
    if game.next_question_task and not game.next_question_task.done():
        game.next_question_task.cancel()

    # Delete previous game messages
    await game.delete_previous_messages(context.bot)

    # Show game summary
    await update.message.reply_text("🛑 Game has been ended by administrator!")
    
    # Calculate game statistics
    total_questions = game.current_question_index + 1
    total_answers = sum(1 for _ in game.answered_players)
    total_correct = len(game.correct_players)
    accuracy = (total_correct / total_answers * 50) if total_answers > 0 else 0

    stats_message = (
        "📊 Game Statistics:\n"
        f"• Questions Completed: {total_questions}/{len(game.questions)}\n"
        f"• Total Players: {len(game.players)}\n"
        f"• Overall Accuracy: {accuracy:.1f}%\n"
        "═" * 35 + "\n\n"
    )
    
    await update.message.reply_text(stats_message)

    # End the game and show results
    await game.end_game(update.message)
    
    # Clear game state
    game.is_active = False
    game.current_question = None
    game.current_question_index = 0
    game.questions = []
    game.message_ids = []
    game.answered_players.clear()
    game.correct_players.clear()

async def show_players(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show list of players who have joined the game"""
    chat_id = update.effective_chat.id
    
    if chat_id not in games:
        await update.message.reply_text("❌ No game has been announced! Use /start_trivia first.")
        return

    game = games[chat_id]
    
    if len(game.players) == 0:
        await update.message.reply_text("📝 No players have joined yet!\n\nUse /join_trivia to join the game.")
        return

    # Create player list message
    player_list = "👥 Current Players List:\n" + "═" * 30 + "\n\n"
    
    # Sort players by join time (using their position in the dictionary)
    for index, (user_id, player) in enumerate(game.players.items(), 1):
        status = "🎮 Playing" if game.is_active else "⏳ Waiting"
        
        # Add additional stats if game is active
        if game.is_active:
            accuracy = (player['correct_answers'] / (game.current_question_index + 1) * 100) if game.current_question_index >= 0 else 0
            player_list += (
                f"{index}. {player['name']}\n"
                f"   {status}\n"
                f"   Points: {player['points']}\n"
                f"   Correct: {player['correct_answers']}/{game.current_question_index + 1} ({accuracy:.1f}%)\n"
                f"   Speed Bonuses: {player['fast_bonuses']}\n"
            )
        else:
            player_list += f"{index}. {player['name']}\n   {status}\n"
        
        player_list += "─" * 30 + "\n"

    # Add summary
    player_list += f"\n📊 Total Players: {len(game.players)}"
    
    if game.is_active:
        player_list += f"\n📍 Current Question: {game.current_question_index + 1}/{len(game.questions)}"

    # Add join instructions
    player_list += "\n\n💡 New players can join anytime with /join_trivia"

    await update.message.reply_text(player_list)    

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
    application.add_handler(CommandHandler("reset_scores", reset_scores))
    application.add_handler(CommandHandler("end_game", force_end_game))
    application.add_handler(CommandHandler("players", show_players))
    application.add_handler(CallbackQueryHandler(handle_answer, pattern="^answer_"))

    # Start the bot
    application.run_polling()

if __name__ == '__main__':
    main()