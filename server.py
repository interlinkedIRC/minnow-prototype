#!/usr/bin/env python3

import time
import asyncio
import re

from random import randint

from crypt import crypt, mksalt
from hmac import compare_digest
import ssl
import logging

from user import User
from group import Group
from storage import UserStorage
from config import *
from errors import *
import parser

logging.basicConfig(level=log_level)
logger = logging.getLogger(__name__)

# This is subject to change
valid_handle = re.compile(r'^[^#!=&$,\?\*\[\]][^=$,\?\*\[\]]+$')

# Flags for the annotations
UNREG = 1
SIGNON = 2

class DCPServer:
    def __init__(self, name, servpass=servpass):
        self.name = name
        self.servpass = servpass

        self.users = dict()
        self.groups = dict()

        f = None

        # A list of lists
        self.motd = []
        try:
            f = open('motd.txt', 'r')

            # A pessimistic guess
            # (max name len + server name + seps + cmd + other gunk)
            curlen = baselen = len(self.name) + 128
            curframe = []
            for line in f:
                if line == '':
                    break

                line = line.rstrip()
                if not line: line = ' '

                if len(line) > 200:
                    # Cap it for the love of god
                    line = line[:200]

                # 6 is motd\0...\0
                llen = len(line) + 6
                if llen + curlen > parser.MAXFRAME:
                    self.motd.append(curframe)
                    curframe = []
                    curlen = baselen

                curlen += llen
                curframe.append(line)

            self.motd.append(curframe)
        except Exception:
            pass
        finally:
            if f: f.close()

        self.user_store = UserStorage()

    def error(self, dest, command, reason, fatal=True, extargs=None):
        if hasattr(dest, 'proto'):
            proto = dest.proto
        elif hasattr(dest, 'error'):
            proto = dest

        if fatal:
            proto = getattr(dest, 'proto', dest)
            logger.debug('Fatal error encountered for client %r (%s: %s [%r])',
                         proto.peername, command, reason, extargs)

        proto.error(command, reason, fatal, extargs)

    def process(self, proto, data):
        # Turn a protocol into a user
        for line in parser.Frame.parse(data):
            command = line.command.replace('-', '_')
            func = getattr(self, 'cmd_' + command, None)
            if func is None:
                self.error(proto, line.command, 'No such command', False)
                return

            req = func.__annotations__.get('return', SIGNON)
            if req & SIGNON:
                if not proto.user:
                    self.error(proto, line.command, 'You are not registered',
                               False)
                    return

                proto_or_user = proto.user
            elif req & UNREG:
                if proto.user:
                    self.error(proto, line.command, 'This command is only ' \
                               'usable before registration', False)
                    return

                proto_or_user = proto

            try:
                # XXX not sure I like this proto_or_user hack
                func(proto_or_user, line)
            except (UserError, GroupError) as e:
                logger.warn('Possible bug hit! (Exception below)')
                traceback.print_exception(e)
                self.error(proto_or_user, line.command, str(e), False)
            except Exception as e:
                logger.exception('Bug hit! (Exception below)')
                self.error(proto_or_user, line.command, 'Internal server ' \
                           'error (this isn\'t your fault)')

    def user_enter(self, proto, name, gecos, acls, properties, options):
        user = User(proto, name, gecos, acls, properties, options)
        proto.user = self.users[name] = user

        # Cancel the timeout
        proto.callbacks['signon'].cancel()
        del proto.callbacks['signon']

        kval = {
            'name' : [self.name],
            'time' : [str(round(time.time()))],
            'version': ['Minnow prototype server', 'v0.1-prealpha'],
            'options' : [],
        }
        user.send(self, user, 'signon', kval)

        # Send the MOTD
        self.cmd_motd(user, line)

        # Ping timeout stuff
        user.timeout = False
        self.ping_timeout(user)

    def user_exit(self, user):
        if user is None:
            return

        del self.users[user.name]

        for group in list(user.groups):
            # Part them from all groups
            group.member_del(user, permanent=True)

        for cb in user.proto.callbacks.values():
            cb.cancel()

    def cmd_signon(self, proto, line) -> UNREG:
        if self.servpass:
            rservpass = line.kval.get('servpass', [None])[0]
            if rservpass != self.servpass:
                self.error(proto, line.command, 'Bad server password')
                return

        name = line.kval.get('handle', [None])[0]
        if name is None:
            self.error(proto, line.command, 'No handle')
            return

        if valid_handle.match(name) is None:
            self.error(proto, line.command, 'Invalid handle', True,
                       {'handle' : [name]})
            return

        if len(name) > 48:
            self.error(proto, line.command, 'Handle is too long', True,
                       {'handle' : [name]})
            return

        # Retrieve the user info
        uinfo = self.user_store.get(name)
        if uinfo is None:
            self.error(proto, line.command, 'You are not registered with ' \
                       'the server', False, {'handle' : [name]})
            return

        password = crypt(line.kval.get('password', ['*'])[0], uinfo.hash)
        if not compare_digest(password, uinfo.hash):
            self.error(proto, line.command, 'Invalid password')
            return

        if name in self.users:
            # TODO - burst all state to the user
            self.error(proto, line.command, 'No multiple users at the '\
                       'moment', True, {'handle' : [name]})
            return

        options = line.kval.get('options', [])

        self.user_enter(proto, name, uinfo.gecos, uinfo.acls, uinfo.properties,
                        options)

    def cmd_register(self, proto, line) -> UNREG:
        if self.servpass:
            rservpass = line.kval.get('servpass', [None])[0]
            if rservpass != self.servpass:
                self.error(proto, line.command, 'Bad server password')
                return

        name = line.kval.get('handle', [None])[0]
        if name is None:
            self.error(proto, line.command, 'No handle')
            return

        if valid_handle.match(name) is None:
            self.error(proto, line.command, 'Invalid handle', False,
                       {'handle' : [name]})
            return

        if len(name) > 48:
            self.error(proto, line.command, 'Handle is too long', False,
                       {'handle' : [name]})
            return

        if self.user_store.get(name) is not None:
            self.error(proto, line.command, 'Handle already registered', False,
                       {'handle' : [name]})
            return

        gecos = line.kval.get('gecos', [name])[0]
        if len(gecos) > 48:
            self.error(proto, line.command, 'GECOS is too long', False,
                       {'gecos' : [gecos]})
            return

        password = line.kval.get('password', [None])[0]
        if password is None or len(password) < 5:
            # Password is not sent back for security reasons
            self.error(proto, line.command, 'Bad password', False)
            return

        password = crypt(password, mksalt())

        # Bang
        self.user_store.add(name, password, gecos, set())

        kval = {
            'handle' : [name],
            'gecos' : [gecos],
            'message' : ['Registration successful, beginning signon'],
        }
        proto.send(self, None, line.command, kval)

        options = line.kval.get('options', [])

        self.user_enter(proto, name, gecos, set(), set(), options)

    def cmd_message(self, user, line) -> SIGNON:
        proto = user.proto
        target = line.target
        if target == '*':
            self.error(user, line.command, 'No valid target', False)
            return

        # Lookup the target...
        if target.startswith(('=', '&')):
            self.error(user, line.command, 'Cannot message servers yet, sorry',
                       False, {'target' : [target]})
            return
        elif target.startswith('#'):
            if target not in self.groups:
                self.error(user, line.command, 'No such group', False,
                           {'target' : [target]})
                return

            target = self.groups[target]
        else:
            if target not in self.users:
                self.error(user, line.command, 'No such user', False,
                           {'target' : [target]})
                return

            target = self.users[target]

        # Get our message
        message = line.kval.get('body', [''])

        # Bam
        target.message(user, message)

    def cmd_motd(self, user, line) -> SIGNON:
        if not self.motd:
            user.send(self, user, 'motd', {})
            return

        total = str(len(self.motd))

        for i, block in enumerate(self.motd):
            kval = {
                'text' : block,
                'multipart' : ['*'],
                'part' : [str(i + 1)],
                'total' : [total],
            }
            user.send(self, user, 'motd', kval)

    def cmd_whois(self, user, line) -> SIGNON:
        target = line.target
        if target == '*' or target.startswith(('=', '#')):
            self.error(user, line.command, 'No valid target', False)
            return

        if target not in self.users:
            self.error(user, line.command, 'No such user', False)
            return

        user = self.users[target]

        kval = {
            'handle' : [user.name],
            'gecos' : [user.gecos],
        }

        if user.has_acl('user:auspex'):
            kval['acl'] = sorted(user.acl)

        if user.groups:
            kval['groups'] = [group for group in user.groups if not 
                              (group.has_property('private') and not
                               user.has_acl('user:auspex'))]

        # FIXME - if WHOIS info is too big, split it up
        
        user.send(self, user, 'whois', kval)

    def cmd_group_enter(self, user, line) -> SIGNON:
        target = line.target
        if target == '*':
            self.error(user, line.command, 'No valid target', False)
            return

        if not target.startswith('#'):
            self.error(user, line.command, 'Invalid group', False,
                       {'target' : [target]})
            return

        if len(target) > 48:
            self.error(user, line.command, 'Group name too long', False,
                       {'target' : [target]})
            return

        if target not in self.groups:
            logger.info('Creating group %s', target)
            self.groups[target] = Group(target)

        group = self.groups[target]
        if group in user.groups:
            assert user in group.users
            self.error(user, line.command, 'You are already entered', False,
                       {'target' : [target]})
            return

        group.member_add(user, line.kval.get('reason', [''])[0])

    def cmd_group_exit(self, user, line) -> SIGNON:
        target = line.target
        if target == '*':
            self.error(user, line.command, 'No valid target', False)
            return

        if not target.startswith('#') or target not in self.groups:
            self.error(user, line.command, 'Invalid group', False,
                       {'target' : [target]})
            return

        group = self.groups[target]
        if group not in user.groups:
            assert user not in group.users
            self.error(user, line.command, 'You are not in that group', False,
                       {'target' : [target]})
            return

        group.member_del(user, line.kval.get('reason', ['']))

    def cmd_pong(self, user, line) -> SIGNON:
        user.timeout = False

    def ping_timeout(self, user) -> SIGNON:
        if user.timeout:
            logger.debug('User %r timed out', user.proto.peername)
            self.error(user, 'ping', 'Ping timeout')
            return

        user.send(self, user, 'ping', {'time' : [str(round(time.time()))]})

        user.timeout = True

        loop = asyncio.get_event_loop()
        sched = randint(4500, 6000) / 100
        cb = loop.call_later(sched, self.ping_timeout, user)
        user.proto.callbacks['ping'] = cb

    def conn_timeout(self, proto) -> UNREG:
        if proto.user:
            proto.callbacks.pop('signon', None)
            return

        self.error(proto, '*', 'Timed out')

server = DCPServer(servname)

class DCPProto(asyncio.Protocol):
    """ This is the asyncio connection stuff...

    Everything should just call back to the main server/user stuff here.
    """

    def __init__(self):
        self.__buf = b''

        # Global state
        self.server = server

        # User state
        self.user = None

        # Callbacks
        self.callbacks = dict()

        # Peer name
        self.peername = None

        self.transport = None

    def connection_made(self, transport):
        self.peername = transport.get_extra_info('peername')
        logger.info('Connection from %s', self.peername)

        self.transport = transport

        # Start the connection timeout
        loop = asyncio.get_event_loop()
        cb = loop.call_later(60, self.server.conn_timeout, self)
        self.callbacks['signon'] = cb

    def connection_lost(self, exc):
        logger.info('Connection lost from %r (reason %s)', self.peername, str(exc))

        self.server.user_exit(self.user)

    def data_received(self, data):
        data = self.__buf + data

        if not data.endswith(b'\x00\x00'):
            data, sep, self.__buf = data.rpartition(b'\x00\x00')
            if sep:
                data += sep
            else:
                self.__buf = data
                return

        try:
            server.process(self, data)
        except ParserError as e:
            self.error('*', 'Parser failure', {'reason' : [str(e)]}, False)
        except Exception as e:
            logger.exception('Bug hit during processing! (Exception below)')
            self.error('*', 'Internal server error (This isn\'t your fault)')

    @staticmethod
    def _proto_name(target):
        if isinstance(target, (User, Group, DCPProto)):
            # XXX for now # is implicit with Group.
            # this is subject to change
            return target.name
        elif isinstance(target, DCPServer):
            return '=' + server.name
        elif target is None:
            return '*'
        else:
            return '&' + getattr(target, 'name', target)

    def send(self, source, target, command, kval=None):
        source = self._proto_name(source)
        target = self._proto_name(target)
        if kval is None: kval = dict()

        frame = parser.Frame(source, target, command, kval)
        self.transport.write(bytes(frame))

    def error(self, command, reason, fatal=True, extargs=None):
        kval = {
            'command' : [command],
            'reason' : [reason],
        }
        if extargs:
            kval.update(extargs)

        self.send(self.server, self.user, 'error', kval)

        if fatal:
            self.transport.close()

# Set up SSL context
ctx = ssl.SSLContext(ssl.PROTOCOL_TLSv1_2)
ctx.load_default_certs(ssl.Purpose.CLIENT_AUTH)
ctx.load_cert_chain('cert.pem')

ctx.options &= ~ssl.OP_ALL
ctx.options |= ssl.OP_SINGLE_DH_USE | ssl.OP_SINGLE_ECDH_USE
ctx.options |= (ssl.OP_NO_SSLv2 | ssl.OP_NO_SSLv3 | ssl.OP_NO_TLSv1 |
                ssl.OP_NO_TLSv1_1)
ctx.options |= ssl.OP_NO_COMPRESSION

loop = asyncio.get_event_loop()
coro = loop.create_server(DCPProto, *listen, ssl=ctx)
_server = loop.run_until_complete(coro)
logger.info('Serving on %r', _server.sockets[0].getsockname())

try:
    loop.run_forever()
except KeyboardInterrupt:
    logger.info('Exiting from ctrl-c')
finally:
    _server.close()
    loop.close()
