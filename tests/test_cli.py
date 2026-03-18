# test_cli.py
# Copyright RomanAILabs - Daniel Harding
import pytest
from romatask.cli import Task, load_config

def test_task_init():
    task = Task()
    assert task.id is not None
    assert task.model == load_config()['default_model']

def test_config_load():
    config = load_config()
    assert 'default_model' in config
