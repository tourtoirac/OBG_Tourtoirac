log_format = "{time:MMMM D,YYYY:HH:mm:ss} | {level:<8} | {name}:{function}:{line} - {message} | {extra}"
log_level = 'debug'

params = {
    "SERVICE_ENVIRONMENT": 'localhost_waterloo',
    "ws_addr": '127.0.0.1',
    "ws_port": 9000,
    "back_api": {
        "host": 'http://chabanas',
        "access_key": "access_key",
    },
    "SEND_KEEPALIVE": True,
    "looped_keep_alive": 30,
    "CONSOLE_LOG_LEVEL": "DEBUG",
    "allowed_games" : [
        "Waterloo"
    ]
}