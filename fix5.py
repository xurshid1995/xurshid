content = open('d:/hisobot/Xurshid/templates/sales.html', encoding='utf-8').read()

old = "const buttonEmoji = quantity <= 0 ? '?' : '??';"
new = "const buttonEmoji = quantity <= 0 ? '\u26d4' : '\ud83d\uded2';"
count = content.count(old)
print('Count:', count)
content = content.replace(old, new)
open('d:/hisobot/Xurshid/templates/sales.html', 'w', encoding='utf-8').write(content)
print('Done')
