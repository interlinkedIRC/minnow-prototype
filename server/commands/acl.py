# coding=utf-8
# Copyright © 2014 Elizabeth Myers, Andrew Wilcox. All rights reserved.
# This software is free and open source. You can redistribute and/or modify it
# under the terms of the Do What The Fuck You Want To Public License, Version
# 2, as published by Sam Hocevar. See the LICENSE file for more details.

import asyncio

from server.command import Command, register
from server.acl import UserACLValues, GroupACLValues


class ACLBase:
    @asyncio.coroutine
    def has_grant_group(self, server, user, gtarget, acl):
        if user not in gtarget.users:
            return (False, 'Must be in group to alter ACL\'s in it')

        check_grant = ['grant', 'grant:*']
        check_grant.extend('grant:' + a for a in acl)
        if gtarget.acl.has_any(check_grant):
            return (True, None)
        else:
            if not gtarget.acl.has_acl('group:grant'):
                return (False, 'No permission to alter ACL')

        return (True, None)

    @asyncio.coroutine
    def has_grant_user(self, server, user, utarget, acl):
        check_grant = ['user:grant']
        check_grant.extend(acl)
        if not gtarget.acl.has_acl_all(check_grant):
            return (False, 'No permission to alter ACL')

        return (True, None)

    @asyncio.coroutine
    def has_grant(self, server, user, gtarget, utarget, acl):
        target = getattr(target, 'name', target)

        if target[0] == '#':
            ret = (yield from self.has_grant_group(server, user, gtarget,
                                                   acl))
        else:
            ret = (yield from self.has_grant_user(server, user, utarget, acl))

        return ret

    @asyncio.coroutine
    def registered(self, server, user, proto, line):
        if 'acl' not in line.kval or not line.kval['acl']:
            server.error(user, line.command, 'No ACL', False,
                         {'target': [target]})
            return (None, None)

        # Obtain target info
        line.kval['acl'] = acl = [a.lower() for a in line.kval['acl']]
        line.target = target = line.target.lower()
        if target == '*':
            server.error(user, line.command, 'No valid target', False,
                         {'acl': acl})
            return (None, None)
        elif target[0] == '#':
            if acl not in GroupACLValues:
                server.error(user, line.command, 'Invalid ACL', False,
                             {'target': [target], 'acl': acl})
                return (None, None)

            gtarget = (yield from server.get_any_target(target))
            utarget = line.kval.get('user')

            if not utarget:
                server.error(user, line.command, 'No valid user for target',
                             False, {'target': [target], 'acl': acl})
                return (None, None)

            utarget = (yield from server.get_any_target(utarget.lower()))
        elif target[0] == '=':
            server.error(user, line.command, 'ACL\'s can\'t be set on '
                         'servers yet', False,
                         {'target': [target], 'acl': acl})
            return (None, None)
        else:
            if acl not in UserACLValues:
                server.error(user, line.command, 'Invalid ACL', False,
                             {'target': [target], 'acl': acl})
                return (None, None)

            gtarget = None
            utarget = (yield from server.get_any_target(target))

        return (gtarget, utarget)


class ACLSet(ACLBase, Command):
    @asyncio.coroutine
    def registered(self, server, user, proto, line):
        gtarget, utarget = super().registered(server, user, line)
        if (gtarget, utarget) == (None, None):
            return

        acl = line.kval['acl']

        if gtarget:
            kwds = {'target': [gtarget.name], 'user': [utarget.name]}
        else:
            kwds = {'target': [utarget.name]}

        reason = line.kval.get('reason')
        if reason:
            kwds['reason'] = [reason]

        ret, msg = (yield from self.has_grant(server, user, gtarget, utarget,
                                              acl))
        if not ret:
            server.error(user, line.command, msg, False, kwds)
            return

        # Bam
        try:
            if gtarget:
                gtarget.acl.add(utarget, acl, user, reason)
            else:
                utarget.acl.add(acl, user, reason)
        except ACLExistsError as e:
            server.error(user, line.command, 'ACL exists', False, kwds)
            return

        # Report to the target if they're online
        if gtarget:
            gtarget.send(server, user, line.command, kwds)
        elif utarget.proto:
            utarget.send(server, user, line.command, kwds)

        user.send(server, user, line.command, kwds)

    @asyncio.coroutine
    def ipc(self, server, proto, line):
        gtarget, utarget = super().registered(server, proto, proto, line)
        if (gtarget, utarget) == (None, None):
            return

        acl = line.kval['acl']

        if gtarget:
            kwds = {'target': [gtarget.name], 'user': [utarget.name]}
        else:
            kwds = {'target': [utarget.name]}

        reason = line.kval.get('reason')

        # Bam
        try:
            if gtarget:
                gtarget.acl.add(utarget, acl, proto, reason)
            else:
                utarget.acl.add(acl, proto, reason)
        except ACLExistsError as e:
            server.error(proto, line.command, 'ACL exists', False, kwds)
            return

        # Report to the target if they're online
        if gtarget:
            gtarget.send(server, proto, line.command, kwds)
        elif utarget.proto:
            utarget.send(server, proto, line.command, kwds)

        proto.send(server, None, line.commands, kwds)        

class ACLDel(ACLBase, Command):
    @asyncio.coroutine
    def registered(self, server, user, proto, line):
        gtarget, utarget = super().registered(server, user, line)
        if (gtarget, utarget) == (None, None):
            return

        acl = line.kval['acl']

        if gtarget:
            kwds = {'target': [gtarget.name], 'user': [utarget.name]}
        else:
            kwds = {'target': [utarget.name]}

        reason = line.kval.get('reason')
        if reason:
            kwds['reason'] = [reason]

        ret, msg = (yield from self.has_grant(server, user, gtarget, utarget,
                                              acl))
        if not ret:
            server.error(user, line.command, msg, False, kwds)
            return

        # Bam
        try:
            if gtarget:
                gtarget.acl.delete(utarget, acl)
            else:
                utarget.acl.delete(acl)
        except ACLDoesNotExistError as e:
            server.error(user, line.command, 'ACL does not exist', False, kwds)
            return

        # Report to the target if they're online
        if gtarget:
            gtarget.send(server, user, line.command, kwds)
        elif utarget.proto:
            utarget.send(server, user, line.command, kwds)

        user.send(server, user, line.command, kwds)

    @asyncio.coroutine
    def ipc(self, server, proto, line):
        gtarget, utarget = super().registered(server, proto, line)
        if (gtarget, utarget) == (None, None):
            return

        acl = line.kval['acl']

        if gtarget:
            kwds = {'target': [gtarget.name], 'user': [utarget.name]}
        else:
            kwds = {'target': [utarget.name]}

        reason = line.kval.get('reason')
        if reason:
            kwds['reason'] = [reason]

        # Bam
        try:
            if gtarget:
                gtarget.acl.delete(utarget, acl)
            else:
                utarget.acl.delete(acl)
        except ACLDoesNotExistError as e:
            server.error(proto, line.command, 'ACL does not exist', False, kwds)
            return

        # Report to the target if they're online
        if gtarget:
            gtarget.send(server, proto, line.command, kwds)
        elif utarget.proto:
            utarget.send(server, proto, line.command, kwds)

        proto.send(server, None, line.command, kwds)

class ACLList(ACLBase, Command):
    @staticmethod
    def split_acl(data):
        acl_entry = []
        acl_time = []
        acl_setter = []

        # Split out the ACL info
        for entry in data:
            acl = entry['acl']
            time = entry['timestamp']
            setter = entry['setter']

            if not acl:
                # TODO warning
                continue

            if time is None:
                time = 0

            if setter is None:
                setter = '*'

            acl_entry.append(acl)
            acl_time.append(time)
            acl_setter.append(setter)

        return acl_entry, acl_time, acl_setter

    @asyncio.coroutine
    def registered(self, server, user, proto, line):
        gtarget, utarget = super().registered(server, user, line)
        if (gtarget, utarget) == (None, None):
            return

        acl = line.kval['acl']

        if gtarget:
            kwds = {'target': [gtarget.name], 'user': [utarget.name]}
        else:
            kwds = {'target': [utarget.name]}

        reason = line.kval.get('reason')
        if reason:
            kwds['reason'] = [reason]

        if gtarget:
            # TODO property value for group:grant only ACL viewing
            data = (yield from server.proto_store.get_group_acl(
                    gtarget.name.lower()))
        else:
            # ACL's should only be viewable by those with grant priv for users
            # TODO is this correct?
            ret, msg = (yield from self.has_grant(server, user, gtarget,
                                                  utarget, acl))
            if not ret:
                server.error(user, line.command, msg, False, kwds)
                return

            data = (yield from server.proto_store.get_user_acl(
                    utarget.name.lower()))

        if not data:
            user.send(server, user, line.command, kwds)
            return

        acl_entry, acl_time, acl_setter = self.split_acl(data)
        kwds.update({
            'acl': acl_entry,
            'acl-time': acl_time,
            'acl-setter': acl_setter,
        })
        user.send_multipart(server, user, line.command,
                            ('acl', 'acl-time', 'acl-setter'), kwds)

    @asyncio.coroutine
    def ipc(self, server, proto, line):
        gtarget, utarget = super().registered(server, proto, line)
        if (gtarget, utarget) == (None, None):
            return

        acl = line.kval['acl']

        if gtarget:
            kwds = {'target': [gtarget.name], 'user': [utarget.name]}
        else:
            kwds = {'target': [utarget.name]}

        reason = line.kval.get('reason')
        if reason:
            kwds['reason'] = [reason]

        if gtarget:
            # TODO property value for group:grant only ACL viewing
            data = (yield from server.proto_store.get_group_acl(
                    gtarget.name.lower()))
        else:
            data = (yield from server.proto_store.get_user_acl(
                    utarget.name.lower()))

        if not data:
            proto.send(server, None, line.command, kwds)
            return

        acl_entry, acl_time, acl_setter = self.split_acl(data)
        kwds.update({
            'acl': acl_entry,
            'acl-time': acl_time,
            'acl-setter': acl_setter,
        })
        proto.send_multipart(server, None, line.command,
                             ('acl', 'acl-time', 'acl-setter'), kwds)

register.update({
    'acl-set': ACLSet(),
    'acl-del': ACLDel(),
    'acl-list': ACLList()
})
