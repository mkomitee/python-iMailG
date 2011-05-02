#!/usr/bin/env python
import urllib
import iniparse
import os
import imaplib
import getpass
import re
import sys
import logging
import time

class GMailError(Exception):
    pass

class GMail(object):
    logger = logging.getLogger('GMail')
    config_file = '%s/.iMailG.ini' % os.environ['HOME']
    def __init__(self, address, password):
        self.logger    = self.__class__.logger
        self._address  = address
        self._password = password
        self._retried  = False
        self.__imap    = None
        self._read_config()

    def _read_config(self):
        cfg = iniparse.INIConfig(open(self.__class__.config_file))
        try:
            self._last_uid = cfg[self._address]['last_uid']
        except KeyError:
            self._last_uid = '0'
        try:
            self._server = cfg[self._address]['server']
        except KeyError:
            self._server = 'imap.gmail.com'
        try:
            self._port = cfg[self._address]['port']
        except KeyError:
            self._port = '993'
        try:
            self._badge = cfg[self._address]['badge']
        except KeyError:
            self._badge = '0'
        try:
            self._receipt = cfg[self._address]['receipt']
        except KeyError:
            raise(GMailError('receipt is required in the %s section of %s' % (self._address, self.__class__.config_file)))
        try:
            self._ringtone = cfg[self._address]['ringtone']
        except KeyError:
            self._ringtone = 'default'
        try:
            self._label = cfg[self._address]['label']
        except KeyError:
            self._label = 'INBOX'
        try:
            if cfg[self._address]['send_summary'] == '0':
                self._send_summary = False
            else:
                self._send_summary = True
        except KeyError:
            self._send_summary = True
        try:
            self._url = cfg[self._address]['url']
        except KeyError:
            self._url = 'http://igmail.idemfactor.com/ppush.php'

    @property
    def _imap(self):
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
        self.logger.info("Connecting to %s:%s" % (self._server, self._port))
        self.__imap = imaplib.IMAP4_SSL(self._server, self._port)

        status, message = self.__imap.login(self._address, self._password)
        if status == 'OK':
            self.logger.debug("Logged into gmail: %s" % message)
        else:
            error_msg = "Failure in imap login: %s, %s" % (status, message)
            self.logger.debug(error_msg)
            raise(GMailError(error_msg))

        self.logger.info("Selecting label %s" % self._label)
        status, code = self.__imap.select(self._label)
        if status == 'OK':
            self.logger.debug("Selected %s" % self._label)
        else:
            error_msg = "Failure in imap select: %s, %s" % (status, code)
            self.logger.debug(error_msg)
            raise(GMailError(error_msg))

    def _push(self, badge=None, messages=None, message=None):
        post = dict(email=self._address, txid=self._receipt, igmail='iGmail')

        if message is not None:
            self.logger.info("Sending message: %s" % message)
            post['msg'] = message
        else:
            if badge != int(self._badge):
                # we need to update the badge, at least
                post['badge'] = badge
                if self._send_summary and len(messages) > 0:
                    # New messages to send, ...
                    message = 'From: %(from)s||Subject: %(subject)s' % messages[0]
                    post['msg'] = message[:180]
                    try:
                        post['ringtone'] = self._ringtone
                    except AttributeError:
                        pass
            else:
                # short circuit, theres nothing to update
                self.logger.debug("Nothing to do")
                return

        self._post(post)

    def _post(self, content):
        params = urllib.urlencode(content)
        self.logger.debug("POSTING %s" % params)
        f = urllib.urlopen(self._url, params)
        result = f.read()
        if result != '':
            self.logger.warning(result)



    def _check(self):
        count = 0
        notifications = []
        status, ids = self._imap.search(None, 'UNSEEN')
        if status == 'OK':
            self.logger.debug("Got list of unseen ids")
        else:
            raise(GMailError("Failure in imap search"))

        for id in ids[0].split():
            count += 1
            status, data = self._imap.fetch(id, '(UID BODY.PEEK[HEADER.FIELDS (Subject From)])')
            if status == 'OK':
                self.logger.debug("Fetched data for message %s" % id)
            else:
                raise(GMailError("Failure in imap fetch"))

            m = re.search('UID (\d+) BODY', data[0][0])
            if m is not None:
                uid = m.group(1)
                self.logger.debug("Extracted uid for message %s: %s" % (id, uid))
            else:
                raise(GMailError("No UID for message %s" % id))

            if int(uid) > int(self._last_uid):
                self._last_uid = uid
            else:
                self.logger.debug("Skipping uid %s, it's been seen" % uid)
                continue

            fields = re.split('[\r\n]+', data[0][1])
            msg = {'uid': uid, 'subject': 'No Subject', 'from': 'No From'}
            for field in fields:
                m = re.match('From: (.*)$', field)
                if m is not None:
                    msg['from'] = m.group(1).strip()
                    m = re.match("(.*)<", msg['from'])
                    if m is not None:
                        msg['from'] = m.group(1).strip()
                    self.logger.debug("Extracted from field from message %s: %s" % (id, msg['from']))
                    continue

                m = re.match('Subject: (.*)$', field)
                if m is not None:
                    msg['subject'] = m.group(1).strip()
                    self.logger.debug("Extracted subject field from message %s: %s" % (id, msg['subject']))
                    continue
            notifications.append(msg)

        self.logger.debug("%d unread messages" % count)
        self._push(badge=count, messages=notifications)
        self._badge = str(count)


    def _checkpoint(self):
        # TODO this is possibly a race condition if we have multiple instances
        # running polling multiple mail boxes
        cfg = iniparse.INIConfig(open(self.__class__.config_file))
        cfg[self._address]['last_uid'] = self._last_uid
        f = open(self.__class__.config_file, 'w')
        print >>f, cfg
        f.close()

    def monitor(self, sleep_time=None):
        try:
            if sleep_time is None:
                sleep_time = 30
            while True:
                self._check()
                self._checkpoint()
                time.sleep(sleep_time)
        except Exception as e:
            self.logger.critical(e)
            self._push(message="Unable to monitor inbox")
            raise(e)


if __name__ == '__main__':
    try:
        try:
            mailbox = sys.argv[1]
        except IndexError:
            sys.stderr.write("Please pass in the mailbox to monitor as an argument.")
            sys.exit(1)
        password = getpass.getpass()
        FORMAT = '%(asctime)-15s %(name)s %(levelname)s - %(message)s'
        logging.basicConfig(stream=sys.stderr, format=FORMAT)
        GMail.logger.setLevel(logging.INFO)
        m = GMail(sys.argv[1], password)
        m.monitor(30)
    except KeyboardInterrupt:
        sys.exit(1)
