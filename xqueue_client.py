import time
import json
import random
import logging
import requests
import threading
import multiprocessing

log = logging.getLogger('xserver.' + __name__)


class XQueueClient(object):
    def __init__(self, queue_name, xqueue_server='http://localhost:18040', auth=('lms', 'lms')):
        super(XQueueClient, self).__init__()
        self.session = requests.session()
        self.xqueue_server = xqueue_server
        self.queue_name = queue_name
        self.handlers = []
        self.daemon = True
        self.username, self.password = auth
        self.running = True

    def __repr__(self):
        return '{}({})'.format(self.__class__.__name__, self.queue_name)

    def _parse_response(self, response, is_reply=True):
        if response.status_code not in [200]:
            error_message = "Server %s returned status_code=%d' % (url, r.status_code)"
            log.error(error_message)
            return (False, error_message)

        try:
            xreply = response.json()
        except ValueError:
            error_message = "Could not parse xreply."
            log.error(error_message)
            return (False, error_message)

        if 'return_code' in xreply:
            return_code = xreply['return_code'] == 0
            content = xreply['content']
        elif 'success' in xreply:
            return_code = xreply['success']
            content = xreply
        else:
            return False, "Cannot find a valid success or return code."

        if return_code not in [True, False]:
            return (False, 'Invalid return code.')

        return return_code, content

    def _request(self, method, uri, timeout=10, **kwargs):
        url = self.xqueue_server + uri
        r = None
        while not r:
            try:
                r = getattr(self.session, method)(url, timeout=timeout, **kwargs)
            except requests.exceptions.ConnectionError as e:
                log.error('Could not connect to server at %s in timeout=%f', url, timeout)
                return (False, e.message)
            if r.status_code != 302:
                return self._parse_response(r)
            else:
                if self._login():
                    r = None
                else:
                    return (False, "Could not log in")

    def _login(self):
        url = self.xqueue_server + '/xqueue/login/'
        log.debug("Trying to login to {0} with user: {1} and pass {2}".format(url, self.username, self.password))
        response = self.session.post(url, {
            'username': self.username,
            'password': self.password,
            })
        if response.status_code != 200:
            log.error('Log in error {} {}', response.status_code, response.content)
            return False
        msg = response.json()
        log.debug("login response from %r: %r", url, msg)
        return msg['return_code'] == 0

    def shutdown(self):
        self.running = False

    def add_handler(self, handler):
        self.handlers.append(handler)

    def remove_handler(self, handler):
        self.handlers.remove(handler)

    def _handle_submission(self, content):
        content = json.loads(content)
        for handler in self.handlers:
            result = handler(content)
            if result:
                reply = {'xqueue_body': json.dumps(result),
                         'xqueue_header': content['xqueue_header']}
                status, message = self._request('post', '/xqueue/put_result/', data=reply, verify=False)
                if not status:
                    log.error('Failure for %r -> %r', reply, message)

    def run(self):
        self._login()
        get_params = {'queue_name': self.queue_name}
        while self.running:
            try:
                success, content = self._request('get', '/xqueue/get_submission/', params=get_params)
                if success:
                    result = self._handle_submission(content)
                else:
                    time.sleep(random.randint(1, 4))
            except Exception as e:
                log.exception(e.message)


class XQueueClientThread(XQueueClient, threading.Thread):
    pass


class XQueueClientProcess(XQueueClient, multiprocessing.Process):
    pass