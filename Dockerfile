# Stand-in app image: the app is a few text files.
FROM busybox:1.36.1
COPY app/ /app/
CMD ["sh", "-c", "cat /app/*.txt"]
