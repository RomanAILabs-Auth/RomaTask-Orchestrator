# setup.py
# Copyright RomanAILabs - Daniel Harding
from setuptools import setup, find_packages

setup(
    name='romatask',
    version='1.6.1',
    packages=find_packages(),
    install_requires=[
        'click>=8.1.3',
        'rich>=13.3.5',
        'ollama>=0.1.9',
        'tenacity>=8.2.2',
    ],
    entry_points={
        'console_scripts': [
            'romatask = romatask.cli:cli',
        ],
    },
    author='RomanAILabs - Daniel Harding',
    description='Enterprise-grade AI task orchestration engine with Agentic Loop and Live Streaming.',
)
