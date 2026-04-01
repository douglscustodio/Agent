"""
Module for deduplicating news alerts with a cooldown period of 1 hour.

This module contains the NewsDedupStore class, which manages news alerts and ensures that duplicates are not sent within a specified timeframe.
"""

import time
from collections import defaultdict

COOLDOWN_PERIOD = 3600  # 1 hour in seconds

class NewsDedupStore:
    def __init__(self):
        # Dictionary to hold news alerts with their respective timestamps
        self.news_alerts = defaultdict(float)

    def should_send_news(self, news_id):
        current_time = time.time()
        last_sent_time = self.news_alerts.get(news_id, 0)

        # Check if we can send the news
        if current_time - last_sent_time >= COOLDOWN_PERIOD:
            self.news_alerts[news_id] = current_time  # Update the last sent time
            return True
        return False
