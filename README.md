# FPLReminder

Extracts data from the Fantasy Premier League API and sends messages to Discord -
  * Transfer reminders on the morning of the deadline, and an hour before
  * Transfer summaries showing players transferred in and out of each team in your league, sent after the deadline

FPLReminder is written in Python. 

### Prerequisites
  * All packages in `requirements.txt` installed
  * A Discord bot created and added to the relevant server using the Discord Developer Portal
  * A `.env` file created as per the example, and stored in the same directory as the other files

### Usage
Simply clone the repo, add your `.env` file, and run `reminder.py`. This script will need to continue running for the bot to work - a cloud platform or Raspberry Pi acting as a server is ideal.
The bot will check for an upcoming FPL transfer deadline every morning - if one is detected, the reminder and transfer summary messages will be scheduled.
`fpl.log` will be created on the first run.
