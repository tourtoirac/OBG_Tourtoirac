import requests

from conf import params as PARAMS
from user import User

class Chabanas:
    def __init__(self, logger):
        self.logger = logger
        self.host_url = "http://obg-chabanas:80" # NOSONAR


    def get_game_init_info(self, game_name: str, user: User, key: str):
        game_creation_url = f"{self.host_url}/game/create"
        game_creation_data = {
            "game_name": game_name,
            "player_nickname": user.name,
            "player_key": key
        }
        response = requests.post(game_creation_url, json=game_creation_data)
        self.logger.debug(f"Game info response: {response.status_code}")
        if response.status_code == 201:
            game_dict = response.json()
            game_key = game_dict["session_key"]
            game_info_retrieval_url = f"{self.host_url}/game/get"
            game_info_retrieval_data = {
                "session_code": game_key,
            }
            response = requests.post(game_info_retrieval_url, json=game_info_retrieval_data)
            self.logger.debug(f"Game info response: {response.status_code}")
            if response.status_code == 200:
                return response.json()['session']
            else:
                return False
        else:
            return False


    def get_lobby_active_games(self, sat_list):
        self.logger.debug("Getting lobby active games")
        lobby_games = {}
        game_list_url = f"{self.host_url}/game/list"
        game_list_data = {
            "requested_games": PARAMS['GAMES_LIST'],
            "sat_list": sat_list
        }
        response = requests.post(game_list_url, json=game_list_data)
        self.logger.debug(f"Game lobby response: {response.status_code}")
        if response.status_code == 200:
            lobby_games = response.json()['sessions']
        return lobby_games

    def get_game_info(self, game_name, game_code: str):
        game_creation_url = f"{self.host_url}/{game_name}/get"
        game_creation_data = {
            "game_code": game_code,
        }
        response = requests.post(game_creation_url, json=game_creation_data)
        self.logger.debug(f"Game info response: {response.status_code}")
        if response.status_code == 200:
            return response.json()['game']
        else:
            return False
