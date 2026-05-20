with open('app.py', 'rb') as f:
    content = f.read()

# Store emoji: ğŸ(ctrl)ª -> 🏪 (U+1F3AA)
store_bad  = b'\xc4\x9f\xc5\xb8\xc2\x8f\xc2\xaa'
store_good = '\U0001F3EA'.encode('utf-8')   # 🏪 convenience store

# Warehouse emoji: ğŸ"¦ -> 📦 (U+1F4E6)
warehouse_bad  = b'\xc4\x9f\xc5\xb8\xe2\x80\x9c\xc2\xa6'
warehouse_good = '\U0001F4E6'.encode('utf-8')  # 📦 package

print(f"Store bad bytes:     {store_bad!r}")
print(f"Store good bytes:    {store_good!r}")
print(f"Warehouse bad bytes: {warehouse_bad!r}")
print(f"Warehouse good bytes:{warehouse_good!r}")

count_s = content.count(store_bad)
count_w = content.count(warehouse_bad)
print(f"\nStore emoji occurrences:     {count_s}")
print(f"Warehouse emoji occurrences: {count_w}")

content = content.replace(store_bad, store_good)
content = content.replace(warehouse_bad, warehouse_good)

with open('app.py', 'wb') as f:
    f.write(content)

print("\nDone! app.py updated.")
