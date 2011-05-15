#!/usr/bin/env python
# Copyright 2011 Michael Komitee. All rights reserved.
# 
# Redistribution and use in source and binary forms, with or without modification, are
# permitted provided that the following conditions are met:
# 
#    1. Redistributions of source code must retain the above copyright notice, this list of
#       conditions and the following disclaimer.
# 
#    2. Redistributions in binary form must reproduce the above copyright notice, this list
#       of conditions and the following disclaimer in the documentation and/or other materials
#       provided with the distribution.
# 
# THIS SOFTWARE IS PROVIDED BY MICHAEL KOMITEE ``AS IS'' AND ANY EXPRESS OR IMPLIED
# WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND
# FITNESS FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL MICHAEl KOMITEE  OR
# CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR
# CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
# SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON
# ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING
# NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF
# ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
# 
# The views and conclusions contained in the software and documentation are those of the
# authors and should not be interpreted as representing official policies, either expressed
# or implied, of Michael Komitee. 
import urllib
import iniparse
import os
import imaplib
import getpass
import re
import sys
import logging
import time
import dateutil.parser
import datetime
import email.header
from optparse import OptionParser

__version__ = "1.0"

class iMailGError(Exception):
    pass

class iMailG(object):
    logger = logging.getLogger('iMailG')
    config_file = '%s/.iMailG.ini' % os.environ['HOME']
    def __init__(self, password):
        self._password  = password
        self.logger     = self.__class__.logger
        self._retried   = False
        self.__imap     = None
        self._blacklist = []
        self._whitelist = []
        self._read_config()

    def _read_config(self):
        ''' 
        Read in configuration parameters from the config file
        '''
        cfg = iniparse.INIConfig(open(self.__class__.config_file))
        try:
            self._address = cfg['settings']['address']
        except KeyError:
            raise(iMailGError('address is required in the settings section of %s' % self.__class__.config_file))
        try:
            self._last_uid = cfg['settings']['last_uid']
        except KeyError:
            self._last_uid = '0'
        try:
            self._server = cfg['settings']['server']
        except KeyError:
            self._server = 'imap.gmail.com'
        try:
            self._port = cfg['settings']['port']
        except KeyError:
            self._port = '993'
        try:
            self._badge = cfg['settings']['badge']
        except KeyError:
            self._badge = '0'
        try:
            self._receipt = cfg['settings']['receipt']
        except KeyError:
            raise(iMailGError('receipt is required in the settings section of %s' % self.__class__.config_file))
        try:
            self._ringtone = cfg['settings']['ringtone']
        except KeyError:
            self._ringtone = 'default'
        try:
            self._label = cfg['settings']['label']
        except KeyError:
            self._label = 'INBOX'
        try:
            if cfg['settings']['send_summary'] == '0':
                self._send_summary = False
            else:
                self._send_summary = True
        except KeyError:
            self._send_summary = True
        try:
            self._url = cfg['settings']['url']
        except KeyError:
            self._url = 'http://igmail.idemfactor.com/ppush.php'

        try:
            for address in cfg['blacklist']:
                self._blacklist.append(address)
        except KeyError:
            pass
        
        try:
            for address in cfg['whitelist']:
                self._whitelist.append(address)
        except KeyError:
            pass

        try:
            self._quiet_start = dateutil.parser.parse(cfg['settings']['quiet_start']).time()
        except KeyError:
            self._quiet_start = None
        
        try:
            self._quiet_end = dateutil.parser.parse(cfg['settings']['quiet_end']).time()
        except KeyError:
            self._quiet_end = None

    @property
    def _imap(self):
        ''' 
        Ensure the imap connection is alive, if its not, Connect.

        If its still problematic, reraise the exception.
        '''
        try:
            self.__imap.check()
            self._retried = False
        except AttributeError:
            # Special case for initializing.
            self._connect()
            self._retried = True
            return(self._imap)
        except Exception as e:
            self.logger.info(e)
            if self._retried:
                raise(e)
            else:
                self._connect()
                self._retried = True
                return(self._imap)
        return self.__imap

    def _connect(self):
        '''
        Connect to the server and select the appropriate mailbox
        '''
        self.logger.info("Connecting to %s:%s" % (self._server, self._port))
        self.__imap = imaplib.IMAP4_SSL(self._server, self._port)

        status, message = self.__imap.login(self._address, self._password)
        if status == 'OK':
            self.logger.debug("Logged into gmail: %s" % message)
        else:
            error_msg = "Failure in imap login: %s, %s" % (status, message)
            self.logger.debug(error_msg)
            raise(iMailGError(error_msg))

        self.logger.info("Selecting label %s" % self._label)
        status, code = self.__imap.select(self._label)
        if status == 'OK':
            self.logger.debug("Selected %s" % self._label)
        else:
            error_msg = "Failure in imap select: %s, %s" % (status, code)
            self.logger.debug(error_msg)
            raise(iMailGError(error_msg))

    def _push(self, badge=None, messages=None, message=None):
        '''
        Push the given notification to iMailG
        '''
        post = dict(email=self._address, txid=self._receipt, igmail='iGmail')
        quiet = False

        if self._quiet_start is not None and self._quiet_end is not None:
            now = datetime.datetime.now().time()
            if self._quiet_start < self._quiet_end:
                if now > self._quiet_start and now < self._quiet_end:
                    quiet = True
            else:
                if now > self._quiet_start or now < self._quiet_end:
                    quiet = True
        if quiet:
            self.logger.debug("Squelching alerts due to time")

        if message is not None:
            self.logger.info("Sending message: %s" % message)
            post['msg'] = message
        else:
            if badge != int(self._badge):
                # we need to update the badge, at least
                post['badge'] = badge
                if not quiet and self._send_summary and len(messages) > 0:
                    # New messages to send, ...
                    message = 'From: %(from)s||Subject: %(subject)s' % messages[0]
                    post['msg'] = message[:180]
                    try:
                        post['ringtone'] = self._ringtone
                    except AttributeError:
                        pass
            else:
                # short circuit, theres nothing to update
                self.logger.debug("Nothing to push")
                return

        self._post(post)

    def _post(self, content):
        '''
        Post the given parameters to the url 
        '''
        params = urllib.urlencode(content)
        self.logger.debug("POSTING %s" % params)
        f = urllib.urlopen(self._url, params)
        result = f.read()
        if result != '':
            self.logger.warning(result)


    def _check(self):
        '''
        Extract the appropriate information about new Messages in the mailbox
        '''
        count = 0
        notifications = []
        status, ids = self._imap.search(None, 'UNSEEN')
        if status == 'OK':
            self.logger.debug("Got list of unseen ids")
        else:
            raise(iMailGError("Failure in imap search"))

        for id in ids[0].split():
            count += 1
            status, data = self._imap.fetch(id, '(UID BODY.PEEK[HEADER.FIELDS (Subject From)])')
            if status == 'OK':
                self.logger.debug("Fetched data for message %s" % id)
            else:
                raise(iMailGError("Failure in imap fetch"))

            m = re.search('UID (\d+) BODY', data[0][0])
            if m is not None:
                uid = m.group(1)
                self.logger.debug("Extracted uid for message %s: %s" % (id, uid))
            else:
                raise(iMailGError("No UID for message %s" % id))

            if int(uid) > int(self._last_uid):
                self._last_uid = uid
            else:
                self.logger.debug("Skipping uid %s, it's been seen" % uid)
                continue

            fields = re.split('[\r\n]+', data[0][1])
            msg = {'uid': uid, 'subject': 'No Subject', 'from': 'No From'}
            from_address = None
            for field in [self.__class__.decode_header(f) for f in fields]:
                m = re.match('From: (.*)$', field)
                if m is not None:
                    msg['from'] = m.group(1).strip()
                    from_address = m.group(1).strip()
                    m = re.match("(.*)<(.*)>\s*$", msg['from'])
                    if m is not None:
                        msg['from']  = m.group(1).strip()
                        from_address = m.group(2).strip()
                    self.logger.debug("Extracted from field from message %s: %s" % (id, msg['from']))
                    continue

                m = re.match('Subject: (.*)$', field)
                if m is not None:
                    msg['subject'] = m.group(1).strip()
                    self.logger.debug("Extracted subject field from message %s: %s" % (id, msg['subject']))
                    continue

            if self._blacklisted(from_address):
                self.logger.debug("Messages from %s blacklisted" % from_address)
                continue

            if len(self._whitelist) > 0:
                if not self._whitelisted(from_address):
                    self.logger.debug("Messages from %s not whitelisted" % from_address)
                    continue

            notifications.append(msg)

        self.logger.debug("%d unread messages" % count)
        self._push(badge=count, messages=notifications)
        self._badge = str(count)

    def _blacklisted(self, address):
        ''' 
        Returns whether or not an address is blacklisted
        '''
        for pattern in self._blacklist:
            if re.match(pattern, address):
                return True
        return False


    def _whitelisted(self, address):
        ''' 
        Returns whether or not an address is whitelisted
        '''
        for pattern in self._whitelist:
            if re.match(pattern, address):
                return True
        return False

    def _checkpoint(self):
        '''
        Update our config with the uid of the last seen message

        This prevents duplicate notifications on restart
        '''
        # TODO this is possibly a race condition if we have multiple instances
        # running polling multiple mail boxes
        cfg = iniparse.INIConfig(open(self.__class__.config_file))
        cfg['settings']['last_uid'] = self._last_uid
        f = open(self.__class__.config_file, 'w')
        print >>f, cfg
        f.close()

    def list_addresses(self):
        addresses = set()
        status, ids = self._imap.search(None, 'UNSEEN')
        if status == 'OK':
            self.logger.debug("Got list of unseen ids")
        else:
            raise(iMailGError("Failure in imap search"))

        for id in ids[0].split():
            status, data = self._imap.fetch(id, '(UID BODY.PEEK[HEADER.FIELDS (Subject From)])')
            if status == 'OK':
                self.logger.debug("Fetched data for message %s" % id)
            else:
                raise(iMailGError("Failure in imap fetch"))

            m = re.search('UID (\d+) BODY', data[0][0])
            if m is not None:
                uid = m.group(1)
                self.logger.debug("Extracted uid for message %s: %s" % (id, uid))
            else:
                raise(iMailGError("No UID for message %s" % id))

            fields = re.split('[\r\n]+', data[0][1])
            from_address = None
            for field in [self.__class__.decode_header(f) for f in fields]:
                m = re.match('From: (.*)$', field)
                if m is not None:
                    from_address = m.group(1).strip()
                    m = re.match("(.*)<(.*)>\s*$", from_address)
                    if m is not None:
                        from_address = m.group(2).strip()
                    self.logger.debug("Extracted from field from message %s: %s" % (id, from_address))
                    continue
            if from_address is not None:
                addresses.add(from_address)
        print "\n".join(addresses)
                

    def monitor(self, sleep_time=None, retry=0):
        '''
        Monitor the mailbox every 30 seconds.
        
        If an exception is caught, retry with exponential backoff.
        '''
        if retry is None:
            retry = 0
        else:
            self.logger.info("Sleeping %d before retrying, ..." % retry ** 2)
            time.sleep(retry**2)
        try:
            if sleep_time is None:
                sleep_time = 30
            while True:
                self._check()
                self._checkpoint()
                time.sleep(sleep_time)
                retry = 0
        except Exception as e:
            self.logger.critical(e)
            self._push(message="Unable to monitor inbox")
            retry += 1
            self.monitor(sleep_time, retry)
            raise(e)

    @classmethod
    def decode_header(cls, field):
        '''
        Decode email multi-encoding headers
        '''
        parts = []
        for part in email.header.decode_header(field):
            parts.append(part[0])
        assembled = ' '.join(parts)
        return(assembled)

def version():
    """Display current version and exit"""
    print "%s version: %s" % (os.path.basename(__file__), __version__ )
    sys.exit(0)

def parse_options():
    """Process Commandline Arguments"""
    p = OptionParser()
    p.add_option('-v', '--version', action='store_true', dest='version',
            default=False, help='print version')
    p.add_option('--debug', action='store_true', dest='debug',
            default=False, help='debug output')
    p.add_option('--verbose', action='store_true', dest='verbose',
            default=False, help='verbose output')
    p.add_option('--list-addresses', action='store_true', dest='list_addresses',
            default=False, help='list addresses')
    (options, args) = p.parse_args()
    if options.version:
        version()
    if len(args) > 0:
        p.print_help()
        sys.exit(1)
    return options

def loop():
    password = getpass.getpass()
    m = iMailG(password)
    m.monitor(30)

def list_addresses():
    password = getpass.getpass()
    m = iMailG(password)
    m.list_addresses()

if __name__ == '__main__':
        FORMAT = '%(asctime)-15s %(name)s %(levelname)s - %(message)s'
        logging.basicConfig(stream=sys.stderr, format=FORMAT)
        options = parse_options()
        if options.debug:
            iMailG.logger.setLevel(logging.DEBUG)
        elif options.verbose:
            iMailG.logger.setLevel(logging.INFO)
        else:
            iMailG.logger.setLevel(logging.WARNING)
        try:
            if options.list_addresses:
                list_addresses()
            else:
                loop()
        except KeyboardInterrupt:
            sys.exit(1)

# vim: set ft=python ts=4 sw=4 et:
