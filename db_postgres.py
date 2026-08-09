import os
import psycopg

DATABASE_URL = os.environ['DATABASE_URL']

def conn():
    return psycopg.connect(DATABASE_URL, prepare_threshold=None)
