import json
import os
from datetime import datetime, date

class StateManager:
    def __init__(self, data_path=None):
        if data_path is None:
            # Place data.json in the same directory as this file
            dir_path = os.path.dirname(os.path.abspath(__file__))
            data_path = os.path.join(dir_path, "data.json")
        self.data_path = data_path
        self.data = {
            "sleep_state": {
                "is_sleeping": False,
                "sleep_start_time": None,
                "target_sleep_duration": None
            },
            "user_data": {}
        }
        self.load()

    def load(self):
        if os.path.exists(self.data_path):
            try:
                with open(self.data_path, "r", encoding="utf-8") as f:
                    self.data = json.load(f)
            except Exception as e:
                print(f"Error loading state: {e}")
                # Keep default data

    def save(self):
        try:
            with open(self.data_path, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"Error saving state: {e}")

    # Sleep state management
    def is_sleeping(self) -> bool:
        return self.data["sleep_state"]["is_sleeping"]

    def get_sleep_start_time(self) -> datetime:
        time_str = self.data["sleep_state"]["sleep_start_time"]
        if time_str:
            return datetime.fromisoformat(time_str)
        return None

    def get_target_sleep_duration(self) -> float:
        return self.data["sleep_state"]["target_sleep_duration"]

    def start_sleep(self, duration_hours: float):
        self.data["sleep_state"]["is_sleeping"] = True
        self.data["sleep_state"]["sleep_start_time"] = datetime.now().isoformat()
        self.data["sleep_state"]["target_sleep_duration"] = duration_hours
        self.save()

    def end_sleep(self):
        self.data["sleep_state"]["is_sleeping"] = False
        self.data["sleep_state"]["sleep_start_time"] = None
        self.data["sleep_state"]["target_sleep_duration"] = None
        self.save()

    # User affection management
    def _get_user_entry(self, user_id: str, name: str = None) -> dict:
        user_id = str(user_id)
        if user_id not in self.data["user_data"]:
            self.data["user_data"][user_id] = {
                "affection": 50,
                "last_zero_date": None,
                "name": name
            }
        else:
            if name:
                self.data["user_data"][user_id]["name"] = name
        
        entry = self.data["user_data"][user_id]
        
        # Check daily reset for zero affection
        if entry["affection"] == 0 and entry["last_zero_date"]:
            today_str = date.today().isoformat()
            if entry["last_zero_date"] != today_str:
                entry["affection"] = 1
                entry["last_zero_date"] = None
                self.save()
                
        return entry

    def get_affection(self, user_id: str, name: str = None) -> int:
        entry = self._get_user_entry(user_id, name)
        return entry["affection"]

    def change_affection(self, user_id: str, delta: int, name: str = None) -> int:
        entry = self._get_user_entry(user_id, name)
        old_affection = entry["affection"]
        new_affection = old_affection + delta
        new_affection = max(0, min(100, new_affection))
        
        entry["affection"] = new_affection
        if new_affection == 0:
            entry["last_zero_date"] = date.today().isoformat()
        else:
            entry["last_zero_date"] = None
            
        self.save()
        return new_affection

    def is_blocked(self, user_id: str, name: str = None) -> bool:
        # Checks if affection is currently 0 (re-evaluating dates if new day)
        return self.get_affection(user_id, name) == 0
