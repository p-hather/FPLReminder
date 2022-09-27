from dotenv import load_dotenv
import os
import logging
import pathlib
import requests
from datetime import datetime, timedelta, timezone
from time import sleep
from apscheduler.schedulers.blocking import BlockingScheduler


load_dotenv()
WEBHOOK_URL = os.getenv('DISCORD_WEBHOOK')
LEAGUE_ID = os.getenv('FPL_LEAGUE_ID')

dir = pathlib.Path(__file__).parent.resolve()
log_fp = pathlib.Path(dir).joinpath('fpl.log')
logging.basicConfig(level=logging.INFO, filename=log_fp, filemode="a+",
                    format="%(asctime)-15s %(levelname)-8s %(message)s")


def get_json(url):
    response = requests.get(url)
    response.raise_for_status()  # Raises an exception if the request failed
    return response.json()


class FPLReminderBot:
    """
    Uses data from the Fantasy Premier League API to send transfer deadline reminders.
    - Day reminder (sent when the script is run if there's a transfer that day)
    - Hour reminder (sent an hour before the deadline)

    Also sends Gameweek transfers data for each team in a given league.

    Messages are sent to Discord via POST request to a webhook URL (stored in a .env
    file in the same directory as the code). FPL League ID also needs to be provided
    in the .env file.

    Recommended cron schedule - 0 8 * * *
    """

    def __init__(self):
        self.bs_url = 'https://fantasy.premierleague.com/api/bootstrap-static/'
        self.events = get_json(self.bs_url)['events']
        self.local_tz = datetime.now().astimezone().tzinfo
        self.current_gw = None
        self.deadlines = self.get_deadlines()
        self.league_id = LEAGUE_ID
        self.players = self.get_players()
        self.get_transfers_attempts = 0
        self.current_date = datetime.today()
        self.webhook_url = WEBHOOK_URL
        self.scheduler = BlockingScheduler(timezone='Europe/London')

    def get_deadlines(self):
        """GET data on all unfinished FPL events (gameweeks) from the API"""
        logging.info('Calling FPL API to get events data')

        dt_format = '%Y-%m-%dT%XZ'
        dl = {}
        for e in self.events:
            if not e['finished']:
                # Convert deadlines to local timezone
                dt_utc = datetime.strptime(e['deadline_time'], dt_format)
                dt_local = dt_utc.replace(tzinfo=timezone.utc).astimezone(tz=self.local_tz)
                dl[e['id']] = dt_local
        return dl
    
    def get_players(self):
        """GET data on all current FPL players from the API"""
        logging.info('Calling FPL API to get players data')

        all_player_data = get_json(self.bs_url)['elements']
        return {player['id']: {'web_name': player['web_name'], 'team': player['team'], 
            'element_type': player['element_type']} for player in all_player_data}  # element_type = team id
            # Note only 'web_name' is currently used
    
    def get_team(self, id, gw):
        """GET all players on a given FPL team for a given Gameweek"""
        url = f'https://fantasy.premierleague.com/api/entry/{id}/event/{gw}/picks/'
        data = get_json(url)
        return {pick['element'] for pick in data['picks']}  # Return a 'set'
    
    def webhook_message(self, message):
        """Send POST requests to Discord webhook URL"""
        data = {"content": message}
        response = requests.post(self.webhook_url, data=data)
        response.raise_for_status()  # Raises an exception if the request failed
        logging.info('Message sent successfully')  # Response successful if raise_for_status() avoids exception

    def send_transfers(self):
        """Get Gameweek players transfer data for given FPL league ID, and send to the Discord webhook"""
        self.scheduler.shutdown(wait=False)  # Shut down scheduler as not required from this point

        self.get_transfers_attempts += 1
        logging.info(f'Attempting to fetch transfers data - attempt {self.get_transfers_attempts}/3')

        league_data = get_json(f'https://fantasy.premierleague.com/api/leagues-classic/{self.league_id}/standings/')
        league_name = league_data['league']['name']
        logging.info(f"Looking at league '{league_name}'")

        transfers = []

        for team in league_data['standings']['results']:
            team_name = team["entry_name"]
            team_id = team["entry"]
            logging.info(f"Fetching transfers for team '{team_name}'")

            try:
                current_team = self.get_team(team_id, self.current_gw)
            except requests.models.HTTPError as current_gw_exc:
                logging.info(current_gw_exc)

                if self.get_transfers_attempts < 3:
                    logging.info('Possibly too early to fetch current game week - will try again in 1 hour')
                    sleep(3600)  # Wait an hour, then try again
                    return self.get_transfers()
                else:
                    logging.info(f'Failed to fetch current game week on third attempt - cancelling job')
                    return  # Exit process

            try:
                previous_team = self.get_team(team_id, self.current_gw-1)
            except requests.models.HTTPError as previous_gw_exc:
                logging.info(previous_gw_exc)
                logging.info('Previous gameweek not found for team - skipping')
                continue  # Move to next iteration

            transfers_out = previous_team-current_team
            transfers_in = current_team-previous_team
            
            if len(transfers_out)+len(transfers_out) == 0:
                logging.info('No transfers found for gameweek')
                continue  # Move to next iteration

            transfers_out_str = ':x: '+' | '.join([self.players[player_id]['web_name'] for player_id in transfers_out])
            transfers_in_str = ':white_check_mark: '+' | '.join([self.players[player_id]['web_name'] for player_id in transfers_in])
            text = '\n'.join([f"**{team_name}**", transfers_out_str, transfers_in_str])
            transfers.append(text)

        if not transfers:
            logging.info('No gameweek transfers found')
            return
        
        transfers_str = '\n'.join(transfers)
        message = f":wave: Gameweek {self.current_gw} transfers\n{transfers_str}"
        self.webhook_message(message)

    def send_reminder(self, reminder_type, deadline):
        """Orchestrate reminders and send messages to webhook function"""
        logging.info(f"Attempting to send '{reminder_type}' reminder message")

        if reminder_type == 'day':
            deadline_time = deadline.strftime('%-I:%M%p')
            message = f":alarm_clock: Gameweek {self.current_gw} starts today - transfer deadline is {deadline_time}"
            self.webhook_message(message)
        elif reminder_type == 'hour':
            message = f":warning: Warning - one hour until Gameweek {self.current_gw} deadline"
            self.webhook_message(message)
        else:
            return ValueError("Invalid reminder type - expected 'day' or 'hour'")

    def run_process(self):
        """Bring all functions together to run the daily process, including scheduling hour reminder"""

        # Check whether there's a deadline today
        today_gw = [(gw_id, self.deadlines[gw_id]) for gw_id in self.deadlines if
                    self.deadlines[gw_id].date() == self.current_date.date()]

        if not today_gw:
            logging.info('No gameweek transfer deadlines today')
            return

        self.current_gw, deadline = today_gw[0]  # Assumes only one deadline per day
        deadline_ts = deadline.strftime('%I:%M%p')
        if deadline < self.current_date:
            logging.info(f'Transfer deadline today has already passed ({deadline_ts}) - taking no action')
            return

        logging.info(f"Gameweek {self.current_gw} deadline is today at {deadline_ts}")

        # Day reminder
        self.send_reminder('day', deadline)

        # Hour reminder
        hour_remind_time = deadline - timedelta(hours=1)
        logging.info(f"Scheduling hour reminder for {hour_remind_time.strftime('%I:%M%p')}")
        self.scheduler.add_job(self.send_reminder, 'date',
                               run_date=hour_remind_time, args=['hour', deadline])
        
        # Transfers notification
        transfers_send_time = deadline + timedelta(hours=1, minutes=30)
        logging.info(f"Scheduling transfers send for {transfers_send_time.strftime('%I:%M%p')}")
        self.scheduler.add_job(self.send_transfers, 'date', run_date=transfers_send_time)
        
        self.scheduler.start()  # Scheduler is shutdown in send_transfers function


if __name__ == '__main__':
    fpl = FPLReminderBot()
    fpl.run_process()
