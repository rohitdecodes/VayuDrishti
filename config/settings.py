import os
import yaml
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent

DELHI_STATIONS = [
    "RK Puram",
    "Anand Vihar",
    "ITO",
    "IGI Airport",
    "Punjabi Bagh",
    "Mandir Marg",
    "Siri Fort",
    "DTU",
]

DELHI_BBOX = [28.4, 76.8, 28.9, 77.4]

MAX_FORWARD_FILL_HOURS = 3
MAX_MISSING_FRACTION = 0.15

N_ESTIMATORS = 500
LEARNING_RATE = 0.05
NUM_LEAVES = 31
EARLY_STOPPING_ROUNDS = 50

VALIDATION_DAYS = 14

DATA_WINDOW_DAYS = 90
