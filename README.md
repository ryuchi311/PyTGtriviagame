# 🎮 PyTrivia Bot

An exciting, fast-paced Telegram trivia game bot with real-time scoring, speed bonuses, and competitive leaderboards!

## ✨ Features

- 🚀 **Speed-Based Scoring System**: Answer quickly to earn bonus points!
  - Within 5s: +3 points
  - Within 15s: +2 points
  - Within 30s: +1 point

- 🎯 **Dynamic Question Pool**: 
  - Questions from multiple categories
  - Powered by the OpenTDB API
  - HTML-escaped content handling
  - Multiple-choice format

- 🤖 **AI-Powered Explanations**: 
  - Get funny and educational explanations for answers
  - Powered by Google's Gemini AI
  - Learn while you play!

- 📊 **Rich Statistics & Leaderboards**:
  - Real-time game standings
  - All-time hall of fame
  - Detailed player statistics
  - Performance tracking
  - Global rankings

- ⚡ **Real-Time Features**:
  - Join games in progress
  - 60-second question timer
  - Auto-advancing questions
  - Live player stats

## 🛠️ Setup & Installation

1. **Clone the repository**
```bash
git clone https://github.com/yourusername/pytrivia-bot.git
cd pytrivia-bot
```

2. **Install dependencies**
```bash
pip install -r requirements.txt
```

3. **Set up environment variables**
Create a `.env` file with:
```env
TELEGRAM_BOT_TOKEN=your_telegram_bot_token
QUIZ_API_KEY=your_quiz_api_key
GEMINI_API_KEY=your_gemini_api_key
```

4. **Run the bot**
```bash
python Pytrivia.py
```

## 🎮 Game Commands

### Player Commands
- `/start_trivia` - Announce a new game
- `/join_trivia` - Join an active game
- `/leaderboard` - View current rankings
- `/players` - Show active players

### Admin Commands
- `/start_game` - Begin the trivia session
- `/next_game` - Force next question
- `/end_game` - End current game
- `/reset_scores` - Reset all scores

## 🎯 How to Play

1. Use `/start_trivia` to announce a new game
2. Players join using `/join_trivia`
3. Admin starts the game with `/start_game`
4. Questions appear automatically every 60 seconds
5. Players click answer buttons to respond
6. Speed bonuses awarded for quick correct answers
7. Leaderboard updates in real-time
8. Game ends automatically after all questions

## 📊 Scoring System

- Base Points: 1 point per correct answer
- Speed Bonuses:
  - Super Fast (0-5s): +3 points
  - Fast (5-15s): +2 points
  - Quick (15-30s): +1 point

## 🏆 Leaderboard Features

- **Current Game Stats**:
  - Player rankings
  - Correct answers
  - Speed bonuses
  - Accuracy percentages

- **All-Time Rankings**:
  - Total points
  - Games played
  - Average points per game
  - Special titles for top players

## 🔧 Technical Features

- Async/await implementation
- Error handling and data persistence
- Safe file operations with atomic writes
- Modular design with separate game instances
- Admin privilege checking
- Message cleanup and management

## 🤝 Contributing

Contributions welcome! Please feel free to submit a Pull Request.

## 🙏 Acknowledgments

- OpenTDB API for trivia questions
- Google Gemini AI for answer explanations
- python-telegram-bot for Telegram integration
