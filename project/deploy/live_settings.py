
# from __future__ import unicode_literals

SECRET_KEY = "%(secret_key)s"


# Honor the 'X-Forwarded-Proto' header for request.is_secure()
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTOCOL", "https")

