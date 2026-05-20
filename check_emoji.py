with open('app.py', 'rb') as f:
    content = f.read()

needle = b'emoji'
idx = content.find(needle)
count = 0
while idx != -1 and count < 8:
    chunk = content[idx:idx+35]
    print(repr(chunk))
    idx = content.find(needle, idx+1)
    count += 1
