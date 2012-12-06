from __future__ import print_function

import os
import os.path
from base64 import urlsafe_b64decode as b64decode
from uuid import uuid4
from datetime import date, datetime

from tornado import ioloop, web, auth, escape
from tornado.httpclient import HTTPError
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from cyclence.Calendaring import User, Task, Completion

uuidre = r'[\dA-Fa-f]{8}-[\dA-Fa-f]{4}-[\dA-Fa-f]{4}'\
          '-[\dA-Fa-f]{4}-[\dA-Fa-f]{12}'

datere = r'[\d]{4}-[\d]{2}-[\d]{2}'

def parsedate(datestr):
    'Parses a date in ISO8601 format'
    if not datestr:
        return None
    return datetime.strptime(datestr, '%Y-%m-%d').date()

class BaseHandler(web.RequestHandler):

    @property
    def json(self):
        if not hasattr(self, '_json'):
            self._json = escape.json_decode(self.request.body)
        return self._json
    
    @property
    def session(self):
        return self.application.session

    def get_current_user(self):
        if not hasattr(self, '_user'):
            email = self.get_secure_cookie('user')
            if not email:
                return None
            self._user = self.session.query(User).filter(User.email == email).first()
        return self._user

class CyclenceApp(web.Application):
    r'''Customized application for Cyclence that includes database
    initialization'''
    def __init__(self):
        handlers = [(r"/", MainHandler),
                    (r"/auth/google", GoogleHandler),
                    (r"/logout", LogoutHandler),
                    (r"/tasks", TasksHandler),
                    (r"/tasks/({})".format(uuidre), None),
                    (r"/tasks/({})/completions".format(uuidre),
                     CompletionsHandler),
                    (r"/tasks/({})/completions/({})".format(uuidre, datere),
                     CompletionHandler)
                    ]
        settings = dict(
            cookie_secret=os.getenv('CYCLENCE_COOKIE_SECRET'),
            login_url='/',
            template_path='tpl',
            debug=True if os.getenv('CYCLENCE_DEBUG') == 'true' else False,
            static_path=os.path.join(os.path.dirname(__file__), "../../static"),
            )
        web.Application.__init__(self, handlers, **settings)
        connstr = os.getenv('CYCLENCE_DB_CONNECTION_STRING')
        self.session = sessionmaker(bind=create_engine(connstr))()

class TasksHandler(BaseHandler):
    '''Returns tasks json'''
    @web.authenticated
    def get(self):
        '''Get all of a user's tasks'''
        result = []
        for task in self.current_user.tasks:
            t = dict(
                id = task.task_id,
                name = task.name,
                length = task.length.days,
                decay_length = task.decay_length.days,
                duedate = task.duedate.isoformat(),
                allow_early = task.allow_early,
                notes = task.notes,
                dueity = task.dueity,
                last_completed = task.last_completed.isoformat() \
                    if task.last_completed else None,
                points = task.points,
                points_today = task.point_worth(),
                hue = task.hue(),
                tags = list(task.tags),
                )
            result.append(t)
        self.write(escape.json_encode(result))

    @web.authenticated
    def post(self):
        '''Adds a task to the current user'''
        t = Task(self.get_argument('taskname'),
                 int(self.get_argument('length')), 
                 self.get_argument('firstdue'),
                 self.get_argument('allow_early', True),
                 int(self.get_argument('points', 100)),
                 int(self.get_argument('decay_length', self.get_argument('length'))),
                 self.get_argument('tags').replace(',',' ').split(),
                 self.get_argument('notes'))
        t.user_email = self.current_user.email
        self.current_user.tasks.append(t)
        self.session.commit()
        self.redirect('/', status=303)


class CompletionsHandler(BaseHandler):
    @web.authenticated
    def get(self, task_id):
        task = filter(lambda t: t.task_id == task_id, self.current_user.tasks)
        if len(task) == 0:
            raise HTTPError(404, 'Non existant task or not authorized')
        self.write(escape.json_encode([
                    {'completed_on': c.completed_on.isoformat(),
                     'task_id': str(c.task_id),
                     'points_earned': c.points_earned,
                     'recorded_on': c.recorded_on.isoformat(),
                     'days_late': c.days_late} for c in task[0].completions]))


class CompletionHandler(BaseHandler):
    @web.authenticated
    def post(self, task_id, completed_on):
        task = self.session.query(Task).filter(Task.task_id == task_id).one()
        task.complete(parsedate(completed_on))
        self.session.commit()
        self.redirect('/', status=303)

class MainHandler(BaseHandler):
    def get(self):
        if not self.current_user:
            self.render('logged_out.html')
        else:
            self.render('main_page.html',
                        user=self.current_user,
                        today=date.today())

class GoogleHandler(BaseHandler, auth.GoogleMixin):
    @web.asynchronous
    def get(self):
        if self.get_argument("openid.mode", None):
            self.get_authenticated_user(self.async_callback(self._on_auth))
            return
        self.authenticate_redirect()

    def _on_auth(self, user):
        if not user:
            raise web.HTTPError(500, "Google auth failed")
        self.set_secure_cookie('user', user['email'])
        if not self.session.query(User).filter_by(email=user['email']).first():
            usr = User(email=user['email'],
                       name=user.get('name'),
                       firstname=user.get('first_name'),
                       lastname=user.get('last_name'))
            self.session.add(usr)
            self.session.commit()
        self.redirect('/')
        
class LogoutHandler(BaseHandler):
    @web.authenticated
    def get(self):
        self.clear_cookie('user')
        self.redirect('/')
        return
        self.write('You are now logged out!')
        self.write('<p><a href="/auth/google">Log in again</a></p>')

if __name__ == '__main__':
    CyclenceApp().listen(int(os.getenv('CYCLENCE_TORNADO_PORT')))
    ioloop.IOLoop.instance().start()
