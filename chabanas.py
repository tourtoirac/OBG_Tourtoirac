import requests

from conf import params as PARAMS
from user import User

class Chabanas:
    def __init__(self, logger):
        self.logger = logger
        self.host_url = "http://obg-chabanas:80" # NOSONAR


    def create_session(self, game_name: str, user: User, key: str):
        # Try creating a session
        session_creation_url = f"{self.host_url}/session/create"
        session_creation_data = {
            "game_name": game_name,
            "nickname": user.name,
            "key": key
        }
        response = requests.post(session_creation_url, json=session_creation_data)
        self.logger.debug(f"Session info response: {response.status_code}")
        if response.status_code == 201:
            session_dict = response.json()
            session_code = session_dict["session_code"]
            session_info_retrieval_url = f"{self.host_url}/session/get"
            session_info_retrieval_data = {
                "session_code": session_code,
            }
            response = requests.post(session_info_retrieval_url, json=session_info_retrieval_data)
            self.logger.debug(f"Session info response: {response.status_code}")
            if response.status_code == 200:
                return response.json()['session']
            else:
                return False
        else:
            return False


    def join_session(self, session_code: str, user: User, key: str):
        # Call back-end to find out if user can join a session
        session_join_url = f"{self.host_url}/session/join"
        session_join_data = {
            "session_code": session_code,
            "nickname": user.name,
            "key": key
        }
        response = requests.post(session_join_url, json=session_join_data)
        self.logger.debug(f"Session info response: {response.status_code}")
        if response.status_code == 200:
            return response.json()
        else:
            return False


    def get_active_sessions(self, game_name_list, sat_list):
        self.logger.debug("Getting lobby active sessions")
        lobby_sessions = {}
        session_list_url = f"{self.host_url}/session/list"
        session_list_data = {
            "game_name_list": game_name_list,
            "sat_list": sat_list
        }
        response = requests.post(session_list_url, json=session_list_data)
        self.logger.debug(f"Session lobby response: {response.status_code}")
        if response.status_code == 200:
            lobby_sessions = response.json()['sessions']
        return lobby_sessions

    def get_session_info(self, session_code: str):
        session_get_url = f"{self.host_url}/session/get"
        session_get_data = {
            "session_code": session_code,
            "sat_list": ["game_json"]
        }
        response = requests.post(session_get_url, json=session_get_data)
        self.logger.debug(f"Game info response: {response.status_code}")
        if response.status_code == 200:
            return response.json()['session']
        else:
            return False

    def get_game_list(self, game_name_list: list):
        game_list_url = f"{self.host_url}/game/list"
        game_list_data = {
            "game_name_list": game_name_list,
        }
        response = requests.post(game_list_url, json=game_list_data)
        self.logger.debug(f"Game list response: {response.status_code}")
        if response.status_code == 200:
            return response.json()['game_list']
        else:
            return False
