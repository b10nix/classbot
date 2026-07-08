import logging

# System တစ်ခုလုံးစာအတွက် Log Format ကို ဤနေရာမှ ထိန်းချုပ်မည်
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

def get_logger(module_name: str):
    """
    ဖိုင်အသီးသီးမှ Logger ကို လွယ်ကူစွာ ခေါ်ယူအသုံးပြုနိုင်ရန် Helper
    Usage: logger = get_logger(__name__)
    """
    return logging.getLogger(module_name)