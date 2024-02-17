#!/bin/bash

# bash script to build a new python venv

# some const
ENV_DIR="venv"


# check if already exist
if [ -d "$ENV_DIR" ]; then
    echo "virtual environment '$ENV_DIR' already exist, exit."
    exit 1
fi

# init a new one
if python -m venv "$ENV_DIR";
then 
    echo "init new virtual environment in '$ENV_DIR' OK"
else
    echo "an error occur during virtual environment init, exit"
    exit 2
fi

# activate it
if source "./$ENV_DIR/bin/activate";
then 
    echo "activation of new virtual environment OK"
else
    echo "an error occur during virtual environment activation, exit"
    exit 3
fi

# github token request (for private repo)
echo "please enter github token:"
read -s GITHUB_TOKEN

# add some package(s) to venv
echo "add some package(s) to the new virtual environment"
pip install -U pyModbusTCP==0.2.1
pip install -U redis==5.0.1
pip install -U schedule==1.2.1
pip install git+https://sourceperl:${GITHUB_TOKEN}@github.com/sourceperl/pyHMI.git
