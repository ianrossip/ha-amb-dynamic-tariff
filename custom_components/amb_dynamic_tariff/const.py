"""Constants for the AMB Dynamic Tariff integration."""
from datetime import timedelta

DOMAIN = "amb_dynamic_tariff"
NAME = "AMB Dynamic Tariff"

API_URL = "https://www.amb.ch/Umbraco/Api/HivePower/GetChartData/"
AMB_TARIFF_URL = (
    "https://www.amb.ch/privati/elettricita/"
    "tariffe-fornitori-energia-elettrica-bellinzona/"
)

UPDATE_INTERVAL = timedelta(minutes=15)

COLOR_LOW = "#05DA3A"
COLOR_HIGH = "#E3051B"

TARIFF_LOW = "low"
TARIFF_HIGH = "high"
TARIFF_UNKNOWN = "unknown"
