import os
import logging
from dotenv import load_dotenv

# 📌 .env အသစ်ကို အတင်းဖတ်ခိုင်းခြင်း
load_dotenv(override=True)

# 📌 Master IDs
OWNER_ID = int(os.getenv("OWNER_ID", "0"))
LOG_GROUP_ID = int(os.getenv("LOG_GROUP_ID", "0"))

# 📌 System-wide Text Constants
FOOTER = "\n\n<pre>System by Ben | Omni-Net 🟢</pre>"

# 📌 Class Group များကို List အနေဖြင့် တစ်ခါတည်း ဖြတ်ထုတ်ထားခြင်း
def _get_class_groups():
    group_id_str = os.getenv("CLASS_GROUP_ID", "0")
    try:
        return [int(g.strip()) for g in group_id_str.split(",") if g.strip() and g.strip() != "0"]
    except ValueError:
        logging.error("CLASS_GROUP_ID format in .env is invalid.")
        return []

CLASS_GROUP_IDS = _get_class_groups()