#!/bin/bash

# python app launcher

# abort on error
set -e

# activate venv
source ./venv/bin/activate

# run app in venv
python -u app.py