from enum import IntEnum

class State(IntEnum):
    # Confession States
    CONFESS_MSG = 0
    CONFESS_CONFIRM = 1
    
    # Post States
    POST_MSG = 40
    POST_CONFIRM = 41
    
    # Custom Confession (C1) States
    C1_MSG = 4
    C1_NAME = 5
    C1_CONFIRM = 6
    
    # Approver Management
    APPROVER_ADD = 7
    
    # Nickname States
    NICK_MSG = 8
    NICK_CONFIRM = 9