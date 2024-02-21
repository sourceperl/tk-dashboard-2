#!/usr/bin/env python3

import hashlib
import string
import secrets

# generate a strong password with his sha256 hash
symbols = string.ascii_letters + string.digits
pwd = ''.join(secrets.choice(symbols) for _ in range(64))
sha = hashlib.sha256(pwd.encode('utf-8')).hexdigest()

# print results
print(f'pass    {pwd}')
print(f'sha256  {sha}')
