import os
import yaml
from typing import Any

class ConfigHandler:
    _instance = None
    _config = None
    
    DEFAULT_CONFIG = {
        'simulation': {
            'schedulers': ['static', 'dynamic_adab', 'dynamic_acab','dynamic_aimd','rl'],
            'min_buoys': 20,
            'max_buoys': 30,
            'step_buoys': 20,
            'intervals': [1.0, 0.5, 0.25],
            'duration': 600,
            'num_processes': 4,
            'ideal_channel': True,
            'ramp_scenario': False,
            'enable_metrics': True,
            'enable_logging': False,
            'multihop_mode': 'none',  # Options: none, append, forwarded
            'multihop_limit': 2,      # Maximum hops for forwarded mode
        },
        'world': {
            'width': 800.0,
            'height': 800.0
        },
        'buoys': {
            'mobile': True,
            'mobile_percentage': 1.0,
            'default_battery': 100.0,
            'default_velocity': 15.0,
        },
        'network': {
            'bit_rate': 1000000,
            'speed_of_light': 300000000.0,
            'communication_range_max': 120.0,
            'communication_range_high_prob': 70.0,
            'delivery_prob_high': 0.9,
            'delivery_prob_low': 0.15
        },
        'csma': {
            'slot_time': 0.000020,
            'difs_time': 0.000050,
            'cw': 16,
            'backoff_time_min': 0.001,
            'backoff_time_max': 0.016
        },
        'scheduler': {
            'beacon_min_interval': 1.0,
            'beacon_max_interval': 5.0,
            'static_interval': 1.0
        },
        'energy': { #check with prof
            'enable_for_protocols': [],  # e.g., ['rl'] or empty to disable
            'transmission_energy': 0.0044,  # 4.4 mJ per beacon
            'reception_energy': 0.000088,  # 88 µJ per second
            'idle_listening_energy': 0.000088,  # 88 µJ per second
            'min_battery_threshold': 0.0
        }
    }
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(ConfigHandler, cls).__new__(cls)
        return cls._instance
    
    def __init__(self):
        if self._config is None:
            self._load_config()
    
    def _load_config(self):
        config_path = 'config.yaml'
        if os.path.exists(config_path):
            with open(config_path, 'r') as f:
                self._config = yaml.safe_load(f)
        else:
            self._config = self.DEFAULT_CONFIG.copy()
            with open(config_path, 'w') as f:
                yaml.dump(self.DEFAULT_CONFIG, f, default_flow_style=False, sort_keys=False)
    
    def get(self, section: str, key: str) -> Any:
        # Special case: neighbor_timeout is calculated as 3 * static_interval
        if section == 'scheduler' and key == 'neighbor_timeout':
            static_interval = self._config.get('scheduler', {}).get('static_interval', 1.0)
            return 3.0 * static_interval
        return self._config.get(section, {}).get(key)