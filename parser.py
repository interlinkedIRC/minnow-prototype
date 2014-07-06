import json

from collections import defaultdict
from itertools import islice

from errors import *

MAXFRAME = 1400
MAXTARGET = 48

class BaseFrame:
    def __init__(self, source, target, command, kval):
        self.source = source
        self.target = target
        self.command = command
        self.kval = kval

    @classmethod
    def from_other(cls, other):
        return cls(other.source, other.target, other.command, other.kval)

    @staticmethod
    def _generic_len(source, target, command, kval):
        raise NotImplementedError()

    def __len__(self):
        return self._generic_len(self.source, self.target, self.command,
                                 self.kval)

    @staticmethod
    def fit(command, kval):
        """ Get the minimum overhead for a frame with a given set of keys """
        llen = self._generic_len(MAXTARGET, MAXTARGET, command, kval)
        return MAXFRAME - llen

class Frame(BaseFrame):
    terminator = b'\0\0'

    @classmethod
    def parse(cls, text):
        if not text.endswith(cls.terminator) or len(text) < 10:
            raise ParserIncompleteError('Incomplete frame')

        # Grab the llen
        llen = int.from_bytes(text[:2], 'big')
        if llen > MAXFRAME:
            raise ParserSizeError('Frame is too large for the wire')

        # Tokenise
        text = text[3:].split(b'\0')
        if text[-1] == b'':
            del text[-1]

        if len(text) < 3:
            raise ParserInvalidError('Invalid DCP frame')

        source = text[0].decode('utf-8', 'replace').lower()
        target = text[1].decode('utf-8', 'replace').lower()
        command = text[2].decode('utf-8', 'replace').lower()

        # Generate the key/val portion
        kval = defaultdict(list)

        if len(text) > 3:
            if (len(text) - 3) % 2:
                # Pad if we have too few items
                text.append(b'*')

            # This part might be a bit weird for a few...
            # We make a slice of text from the third position to the end,
            # then we duplicate the *same* iterator (same reference and all)
            # and feed it to zip, which will then output keys and values
            # sequentially one by one.
            i = [islice(iter(text), 3, None)] * 2
            for k, v in zip(*i):
                # All keys are lowercase
                k = k.decode('utf-8', 'replace').lower()
                v = v.decode('utf-8', 'replace')
                if v in kval[k]:
                    raise ParserValueError('Duplicate value not allowed')

                kval[k].append(v)

        return cls(source, target, command, kval)

    def __bytes__(self):
        if len(self) > MAXFRAME - 20:
            # Offset is to ensure it fits within JSON too
            raise ParserSizeError('Frame is too large')

        frame = [self.source, self.target, self.command]
        for k, v in self.kval.items():
            for v2 in v:
                frame.extend([k, v2])

        frame.append('\0')

        frame = '\0'.join(frame)

        # has to include the len of the short (2 bytes) + the sep
        llen = int.to_bytes(len(frame) + 3, 2, 'big')

        frame = llen + b'\0' + frame.encode('utf-8', 'replace')
        return frame

    @staticmethod
    def len_kv(kval):
        l = sum(sum((len(k)+len(v2)+2) for v2 in v) for k, v in kval.items())
        return l

    @staticmethod
    def _generic_len(source, target, command, kval):
        # We count the two byte short, and all nulls we know of now (including
        # the end and the known separators)
        llen = 3 + len(source) + 1 + len(target) + 1 + len(command) + 1 + 2

        if len(kval) > 0:
            llen += sum(sum((len(k)+len(v2)+2) for v2 in v) for k, v in 
                            kval.items()) - 1

        return llen

    def __repr__(self):
        fmtstr = 'Frame(source={}, target={}, command={}, kval={})'
        return fmtstr.format(self.source, self.target, self.command, self.kval)


class JSONFrame(BaseFrame):
    terminator = b'\0'

    @classmethod
    def parse(cls, text):
        if not text.endswith(cls.terminator) or len(text) < 10:
            raise ParserIncompleteError('Incomplete frame')

        if len(text) < 20:
            raise ParserSizeError('Frame is too small')

        if len(text) > MAXFRAME:
            raise ParserSizeError('Frame is too large')

        text = text.decode('utf-8', 'replace')

        try:
            load = json.loads(text)
        except Exception as e:
            raise ParserSizeError(str(e))

        try:
            header = load[0]

            source = header['source']
            target = header['target']
            command = header['command']
        except Exception as e:
            raise ParserInvalidError('Bad JSON frame header')

        try:
            kval = defaultdict(list, (load[1] if len(load) > 1 else {}))
            for key, val in kval.items():
                if not isinstance(val, list):
                    # Validate the values
                    raise ParserInvalidError('Value not a list')

                for val2 in val:
                    # Validate all values of the values
                    if not isinstance(val2, str):
                        raise ParserInvalidError('Value in list not a str')
        except Exception as e:
            raise ParserInvalidError('Bad JSON frame key/values')

        return cls(source, target, command, kval)

    def __bytes__(self):
        header = {
            'source' : self.source,
            'target' : self.target,
            'command' : self.command
        }
        dump = (header, self.kval)

        frame = (json.dumps(dump, separators=(',', ':')) + '\0')
        if len(frame) > MAXFRAME:
            raise ParserSizeError('Frame is too big for the wire')

        frame = frame.encode('utf-8', 'ignore')
        return frame

    @staticmethod
    def _generic_len(source, target, command, kval):
        # 44 is the base length of a JSON frame minus keys/values
        # (however it does include quotes)
        baselen = 44 + len(source) + len(target) + len(command)

        if kval:
            for k, v in kval.items():
                # Each key : val introduces a minimum of 6 bytes overhead
                baselen += 6 + len(k)

                # Each value adds at least 3 chars of overhead per item
                for v2 in v:
                    baselen += 3 + len(v2)

                # Adjust for trailing comma
                baselen -= 1

            # Exclude trailing comma
            baselen -= 1
        
        return baselen

    def __repr__(self):
        fmtstr = 'JSONFrame(source={}, target={}, command={}, kval={})'
        return fmtstr.format(self.source, self.target, self.command, self.kval)

