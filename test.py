cam_status = {9: {'H': '2026-06-21T10:24:54', 'LP': '2026-06-20T17:32:19'}, 5: {'H': '2026-06-21T10:24:12', 'LP': '2026-06-21T09:39:31'}, 1: {'H': '2026-06-21T10:24:33', 'LP': '2026-06-20T14:10:28'}, 4: {'H': '2026-06-21T10:24:20', 'LP': '2026-06-20T11:32:53'}, 2: {'H': '2026-06-21T10:24:30', 'LP': '2026-06-21T10:13:38'}, 3: {'H': '2026-06-21T10:24:11', 'LP': '2026-06-21T01:20:46'}, 10: {'H': '2026-06-21T10:24:31', 'LP': '2026-06-21T08:38:09'}}
status = ''
for key, value in sorted(cam_status.items()):
    if len(status) > 0:
        status = status + '\n'
    status = status + '*%02d:* ' % key
    substatus = ''
    istatus = value
    for subkey in istatus:
        print('subkey', subkey)
        if len(substatus) > 0:
            substatus = substatus + ', '
        subvalue = istatus[subkey]
        if subkey.lower() == 'active':
            substatus += 'Active' if subvalue == '1' else 'Inactive'
        else:
            if subkey.lower() == 'continuous mode':
                subkey = 'CM'
            elif subkey.lower() == 'last picture':
                subkey = 'LP'
            elif subkey.lower() == 'version':
                subkey = 'V'
            elif subkey.lower() == 'heartbeat':
                subkey = 'H'
            substatus += '%s: %s' % (subkey, subvalue)
        status = status + substatus
print(status)
