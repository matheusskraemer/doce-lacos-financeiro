release: flask --app wsgi db upgrade && flask --app wsgi setup && flask --app wsgi ensure-user
web: gunicorn wsgi:app --workers 2 --bind 0.0.0.0:$PORT --access-logfile -
