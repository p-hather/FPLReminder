from dotenv import load_dotenv
import os
import logging
import requests
from datetime import datetime, timedelta
from apscheduler.schedulers.blocking import BlockingScheduler


load_dotenv()
WEBHOOK_URL = os.getenv('WEBHOOK')

logging.basicConfig(level=logging.INFO, filename="fpl.log", filemode="a+",
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

    Reminders are sent to Discord via POST request to a webhook URL (stored in a .env
    file in the same directory as the code)

    Recommended cron schedule - 0 8 * * *
    """

    def __init__(self):
        self.bs_url = 'https://fantasy.premierleague.com/api/bootstrap-static/'
        self.events = get_json(self.bs_url)['events']
        self.deadlines = self.get_deadlines()
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
                dt = datetime.strptime(e['deadline_time'], dt_format)
                dl[e['id']] = dt
        return dl

    def webhook_message(self, message):
        """Send POST requests to Discord webhook URL"""

        data = {"content": message}
        response = requests.post(self.webhook_url, data=data)
        response.raise_for_status()  # Raises an exception if the request failed
        logging.info('Message sent successfully')  # Response successful if raise_for_status() avoids exception

    def send_reminder(self, reminder_type, gw_id, deadline):
        """Orchestrate reminders and send messages to webhook function"""
        logging.info(f"Attempting to send '{reminder_type}' reminder message")

        if reminder_type == 'day':
            deadline_time = deadline.strftime('%-I:%M%p')
            message = f":alarm_clock: Gameweek {gw_id} starts today - transfer deadline is {deadline_time}"
            self.webhook_message(message)
        elif reminder_type == 'hour':
            message = f":warning: Warning - one hour until Gameweek {gw_id} deadline"
            self.webhook_message(message)
            self.scheduler.shutdown(wait=False)
        else:
            return ValueError("Invalid reminder type - expected 'day' or 'hour'")

    def run_process(self):
        """Bring all functions together to run the daily process"""

        # Check whether there's a deadline today
        today_gw = [(gw_id, self.deadlines[gw_id]) for gw_id in self.deadlines if
                    self.deadlines[gw_id].date() == self.current_date.date()][0]  # Assumes only one deadline per day

        if not today_gw:
            logging.info('No gameweek transfer deadlines today')
            return

        gw_id, deadline = today_gw
        deadline_ts = deadline.strftime('%I:%M%p')
        if deadline < self.current_date:
            logging.info(f'Transfer deadline today has already passed ({deadline_ts}) - taking no action')

        logging.info(f"Gameweek {gw_id} deadline is today at {deadline_ts}")

        # Day reminder
        self.send_reminder('day', gw_id, deadline)

        # Hour reminder
        hour_remind_time = deadline - timedelta(hours=1)
        logging.info(f"Scheduling hour reminder for {hour_remind_time.strftime('%I:%M%p')}")
        self.scheduler.add_job(self.send_reminder, 'date',
                               run_date=hour_remind_time, args=['hour', gw_id, deadline])
        self.scheduler.start()  # Scheduler is shutdown in send_reminder function


if __name__ == '__main__':
    fpl = FPLReminderBot()
    fpl.run_process()
