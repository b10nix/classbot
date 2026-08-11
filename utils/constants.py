import os
import logging
from dotenv import load_dotenv

load_dotenv(override=True)

# 📌 Master IDs
OWNER_ID = int(os.getenv("OWNER_ID", "0"))
LOG_GROUP_ID = int(os.getenv("LOG_GROUP_ID", "0"))

# 📌 System-wide Text Constants (Italic ပြောင်းထားသည်)
FOOTER = "\n\n<i>• Omni-Net</i>"

# 📌 Random Emoji List
RANDOM_EMOJIS = [
    "🦊", "🐼", "🐯", "🦁", "🐮", "🐷", "🐸", "🐵", "🐔", "🐧", "🦉", "🦄", "🐝", "🦋", "🐙", "🐢", "🐍", "🐳", "🐉", "🌵", 
    "🌻", "🍎", "🍉", "🍓", "🍕", "🍔", "🍩", "⚽", "🎸", "🚗", "🚀", "💎", "🔮", "🎭", "🎨", "🤡", "👻", "👽", "👾", "🤖",
    "🐶", "🐱", "🐭", "🐹", "🐰", "🐻", "🐨", "🐒", "🦍", "🦧", "🦮", "🐕", "🐩", "🐺", "🦝", "🐗", "🐴", "🦓", "🦌", "🦬",
    "🦏", "🦛", "🐘", "🦣", "🐪", "🐫", "🦒", "🦘", "🦥", "🦦", "🦨", "🦡", "🐾", "🦃", "🕊️", "🦅", "🦆", "🦢", "🦜", "🦩",
    "🦚", "🐕‍🦺", "🐈", "🐈‍⬛", "🐊", "🦖", "🦕", "🐡", "🐠", "🐟", "🐬", "🦈", "🦭", "🦀", "🦞", "🦐", "🦑", "🐌", "🐛", "🐜",
    "🐞", "🦗", "🕷️", "🦂", "🦟", "🦠", "🏔️", "🌋", "🏕️", "🏖️", "🏜️", "🏝️", "🏞️", "🪐", "🌟", "🔥", "💧", "🌊", "🌈", "⛄"
]

#EC IDs for assigning EC to users
EC_MAP = {
    "ben": 6598291638, 
    "chan": 1898170145, 
    "jojo": 6948534484
}

def _get_class_groups():
    group_id_str = os.getenv("CLASS_GROUP_ID", "0")
    try:
        return [int(g.strip()) for g in group_id_str.split(",") if g.strip() and g.strip() != "0"]
    except ValueError:
        return []

def _get_vip_users():
    vip_str = os.getenv("VIP_USERS", "")
    try:
        return [int(u.strip()) for u in vip_str.split(",") if u.strip()]
    except ValueError:
        return []

CLASS_GROUP_IDS = _get_class_groups()
VIP_USERS = _get_vip_users()