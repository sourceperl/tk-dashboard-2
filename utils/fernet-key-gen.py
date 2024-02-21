#!/usr/bin/env python3

import base64
import os


# build fernet key for use with python cryptography package
key = base64.urlsafe_b64encode(os.urandom(32))

# print result
print(f'key     {key}')
