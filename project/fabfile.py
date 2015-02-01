
#################################################
# modified version of mezzanine default fabfile #
# configured for deployment to aws ec2          #
# v: 1.3                                        # 
# author: github.com/robcmills                  #
#################################################


from __future__ import print_function, unicode_literals
from future.builtins import open

import os
import re
import sys
from functools import wraps
from getpass import getpass, getuser
# from glob import glob
from contextlib import contextmanager
from posixpath import join

from fabric.api import env, cd, prefix, sudo as _sudo, run as _run, hide, task
from fabric.contrib.files import exists, upload_template
from fabric.colors import yellow, green, blue, red


################
# Config setup #
################

conf = {}
if sys.argv[0].split(os.sep)[-1] in ("fab", "fab-script.py"):
    # Ensure we import settings from the current dir
    try:
        conf = __import__("settings", globals(), locals(), [], 0).FABRIC
        try:
            conf["HOSTS"][0]
        except (KeyError, ValueError):
            raise ImportError
    except (ImportError, AttributeError):
        print("Aborting, no hosts defined.")
        exit()


env.db_host = conf.get("DB_HOST", None)
env.db_name = conf.get("DB_NAME", None)
env.db_user = conf.get("DB_USER", None)
env.db_pass = conf.get("DB_PASS", None)

env.admin_pass = conf.get("ADMIN_PASS", None)
env.user = conf.get("SSH_USER", getuser())
env.password = conf.get("SSH_PASS", None)
env.key_filename = conf.get("SSH_KEY_PATH", None)
env.hosts = conf.get("HOSTS", [""])

env.proj_name = conf.get("PROJECT_NAME", os.getcwd().split(os.sep)[-1])
env.venv_home = conf.get("VIRTUALENV_HOME", "/home/%s" % env.user) # /home/ubuntu
env.venv_path = "%s/%s" % (env.venv_home, env.proj_name) # /home/ubuntu/proj_name
env.proj_dirname = "project"
env.proj_path = "%s/%s" % (env.venv_path, env.proj_dirname) # /home/ubuntu/proj_name/project
env.manage = "%s/env/bin/python %s/project/manage.py" % ((env.venv_path,) * 2)

env.domains = conf.get("DOMAINS", [conf.get("LIVE_HOSTNAME", env.hosts[0])])
env.domains_nginx = " ".join(env.domains)
env.domains_python = ", ".join(["'%s'" % s for s in env.domains])
env.ssl_disabled = ""

env.live_host = conf.get("LIVE_HOSTNAME", env.hosts[0] if env.hosts else None)
env.repo_url = conf.get("REPO_URL", "")
env.git = env.repo_url.startswith("git") or env.repo_url.endswith(".git")
env.reqs_path = conf.get("REQUIREMENTS_PATH", None)
env.uwsgi_port = conf.get("UWSGI_PORT", 8000)
env.locale = conf.get("LOCALE", "en_US.UTF-8")

env.secret_key = conf.get("SECRET_KEY", "")
# env.nevercache_key = conf.get("NEVERCACHE_KEY", "")
env.email_host_user = conf.get("EMAIL_HOST_USER", "")
env.email_host_pass = conf.get("EMAIL_HOST_PASS", "")



##################
# Template setup #
##################

# Each template gets uploaded at deploy time, only if their
# contents have changed, in which case, the reload command is
# also run.

templates = {
    "nginx": {
        "local_path": "deploy/nginx.conf",
        "remote_path": "/etc/nginx/nginx.conf",
        "reload_command": "sudo nginx -s reload",
    },
    "nginx_uwsgi": {
        "local_path": "deploy/nginx_uwsgi.conf",
        "remote_path": "/etc/nginx/sites-enabled/%(proj_name)s.conf",
        "reload_command": "sudo nginx -s reload",
    },
    "settings": {
        "local_path": "deploy/live_settings.py",
        "remote_path": "%(proj_path)s/project/live_settings.py",
    },
    # "gunicorn": {
    #     "local_path": "deploy/gunicorn.conf.py",
    #     "remote_path": "%(proj_path)s/gunicorn.conf.py",
    # },
    "supervisor": {
        "local_path": "deploy/supervisor.conf",
        "remote_path": "/etc/supervisor/conf.d/%(proj_name)s.conf",
        "reload_command": "sudo supervisorctl reload",
    },
}


######################################
# Context for virtualenv and project #
######################################

@contextmanager
def virtualenv():
    """
    Runs commands within the project's virtualenv.
    """
    with cd(env.venv_path): # /home/ubuntu/proj_name
        with prefix("source %s/env/bin/activate" % env.venv_path):
            yield


@contextmanager
def project():
    """
    Runs commands within the project's directory.
    """
    with virtualenv():
        with cd(env.proj_dirname):
            yield


@contextmanager
def update_changed_requirements():
    """
    Checks for changes in the requirements file across an update,
    and gets new requirements if changes have occurred.
    """
    print_command("update_changed_requirements")
    reqs_path = join(env.proj_path, env.reqs_path)
    get_reqs = lambda: run("cat %s" % reqs_path, show=False)
    old_reqs = get_reqs() if env.reqs_path else ""
    yield
    if old_reqs:
        new_reqs = get_reqs()
        if old_reqs == new_reqs:
            # Unpinned requirements should always be checked.
            for req in new_reqs.split("\n"):
                if req.startswith("-e"):
                    if "@" not in req:
                        # Editable requirement without pinned commit.
                        break
                elif req.strip() and not req.startswith("#"):
                    if not set(">=<") & set(req):
                        # PyPI requirement without version.
                        break
            else:
                # All requirements are pinned.
                return
        pip("-r %s/%s" % (env.proj_path, env.reqs_path))


###########################################
# Utils and wrappers for various commands #
###########################################

def _print(output):
    print()
    print(output)
    print()


def print_command(command):
    _print(blue("$ ", bold=True) +
           yellow(command, bold=True) +
           red(" ->", bold=True))


@task
def logs(cmd='tail', *args):
    """
    cmd relevant logs (cat, tail, rm)
    usage: fab logs:cat|tail|rm,[nginx|supervisor]
    """
    if not args:
        args = ['nginx','supervisor']
    if 'nginx' in args:
        if exists('/var/log/nginx/access.log'):
            sudo('%s /var/log/nginx/access.log' % cmd)
        if exists('/var/log/nginx/error.log'):
            sudo('%s /var/log/nginx/error.log' % cmd)
    if 'supervisor' in args:
        if exists('/var/log/supervisor/supervisord.log'):
            sudo('%s /var/log/supervisor/supervisord.log' % cmd)       
        if exists('/var/log/supervisor/uwsgi_%s-stdout*' % env.proj_name):
            sudo('%s /var/log/supervisor/uwsgi_%s-stdout*' % (cmd, env.proj_name))
        if exists('/var/log/supervisor/uwsgi_%s-stderr*' % env.proj_name):
            sudo('%s /var/log/supervisor/uwsgi_%s-stderr*' % (cmd, env.proj_name))           



@task
def conf(*args):
    """
    cat relevant config files
    usage: fab conf[:nginx|supervisor|gunicorn]
    """
    if not args: 
        args = ['nginx','supervisor','gunicorn']
    if 'nginx' in args:
        sudo('cat /etc/nginx/nginx.conf')
        sudo('cat /etc/nginx/sites-enabled/%s.conf' % env.proj_name)
    if 'supervisor' in args:
        sudo('cat /etc/supervisor/conf.d/%s.conf' % env.proj_name)
    if 'gunicorn' in args:
        sudo('cat %s/gunicorn.conf.py' % env.proj_path)       
        

@task
def run(command, show=True):
    """
    Runs a shell comand on the remote server.
    """
    if show:
        print_command(command)
    with hide("running"):
        return _run(command)


@task
def sudo(command, show=True):
    """
    Runs a command as sudo.
    """
    if show:
        print_command(command)
    with hide("running"):
        return _sudo(command)


def log_call(func):
    @wraps(func)
    def logged(*args, **kawrgs):
        header = "-" * len(func.__name__)
        _print(green("\n".join([header, func.__name__, header]), bold=True))
        return func(*args, **kawrgs)
    return logged


def get_templates():
    """
    Returns each of the templates with env vars injected.
    """
    injected = {}
    for name, data in templates.items():
        injected[name] = dict([(k, v % env) for k, v in data.items()])
    return injected


@task
def upload_template_and_reload(name):
    """
    Uploads a template only if it has changed, and if so, reload a
    related service.
    """
    print_command('upload_template_and_reload: ' + name)
    template = get_templates()[name]
    local_path = template["local_path"] # deploy/local_settings.py.template
    if not os.path.exists(local_path):
        project_root = os.path.dirname(os.path.abspath(__file__))
        local_path = os.path.join(project_root, local_path)
    remote_path = template["remote_path"] # %(proj_path)s/local_settings.py
    reload_command = template.get("reload_command")
    owner = template.get("owner")
    mode = template.get("mode")
    remote_data = ""
    if exists(remote_path):
        with hide("stdout"):
            remote_data = sudo("cat %s" % remote_path, show=False)
    with open(local_path, "r") as f:
        local_data = f.read()
        # Escape all non-string-formatting-placeholder occurrences of '%':
        local_data = re.sub(r"%(?!\(\w+\)s)", "%%", local_data)
        if "%(db_pass)s" in local_data:
            env.db_pass = db_pass()
        local_data %= env
    clean = lambda s: s.replace("\n", "").replace("\r", "").strip()
    if clean(remote_data) == clean(local_data):
        return
    upload_template(local_path, remote_path, env, use_sudo=True, backup=False)
    if owner:
        sudo("chown %s %s" % (owner, remote_path))
    if mode:
        sudo("chmod %s %s" % (mode, remote_path))
    if reload_command:
        sudo(reload_command)


@task
def apt(packages):
    """
    Installs one or more system packages via apt.
    """
    return sudo("apt-get install -y -q " + packages)

@task
def pip(packages):
    """
    Installs one or more Python packages within the virtual environment.
    """
    with virtualenv():
        return sudo("pip install %s" % packages)


############
# DATABASE #
############

# TODO : implement these for mysql / sqlite

def db_pass():
    """
    Prompts for the database password if unknown.
    """
    if not env.db_pass:
        env.db_pass = getpass("Enter the database password: ")
    return env.db_pass


# def postgres(command):
#     """
#     Runs the given command as the postgres user.
#     """
#     show = not command.startswith("psql")
#     return run("sudo -u root sudo -u postgres %s" % command, show=show)


# @task
# def psql(sql, show=True):
#     """
#     Runs SQL against the project's database.
#     """
#     out = postgres('psql -c "%s"' % sql)
#     if show:
#         print_command(sql)
#     return out


# @task
# def backup(filename):
#     """
#     Backs up the database.
#     """
#     return postgres("pg_dump -Fc %s > %s" % (env.proj_name, filename))


# @task
# def restore(filename):
#     """
#     Restores the database.
#     """
#     return postgres("pg_restore -c -d %s %s" % (env.proj_name, filename))


@task
def python(code, show=True):
    """
    Runs Python code in the project's virtual environment, with Django loaded.
    """
    setup = "import os; os.environ[\'DJANGO_SETTINGS_MODULE\']=\'settings\';"
    full_code = 'python -c "%s%s"' % (setup, code.replace("`", "\\\`"))
    with project():
        result = run(full_code, show=False)
        if show:
            print_command(code)
    return result


@task
def manage(command):
    """
    Runs a Django management command.
    """
    return run("%s %s" % (env.manage, command))


def static():
    """
    Returns the live STATIC_ROOT directory.
    """
    return python("from django.conf import settings;"
                  "print settings.STATIC_ROOT", show=False).split("\n")[-1]


#########################
# Install and configure #
#########################

@task
@log_call
def apt_update():
    """
    Update system
    """
    sudo("apt-get update -y -q")


@task
@log_call
def install_server_requirements():
    """
    Install server requirements
    """
    apt("nginx python-dev python-setuptools git-core libsqlite3-dev sqlite3 "
        "libjpeg-dev libpq-dev supervisor")
    sudo("easy_install pip")
    sudo("pip install virtualenv")


@task
@log_call
def make_env():
    """
    Create a new virtual environment
    """
    if not exists(env.venv_path): # /home/ubuntu/proj_name
        run("mkdir %s" % env.venv_path)
    with cd(env.venv_path):
        if not exists('env'):
            run("virtualenv env")


@task
@log_call
def ssh_keygen(email):
    """
    Use ssh-keygen to generate a public key
    """
    for key in ['id_dsa.pub', 'id_ecdsa.pub', 'id_ed25519.pub', 'id_rsa.pub']:
        if exists('~/.ssh/%s' % key):
            print_command('A public key already exists')
            return
    run("ssh-keygen -t rsa -C %s" % email)
    # start the ssh-agent in the background
    run("eval `ssh-agent -s`")
    # Agent pid 12345
    run("ssh-add ~/.ssh/id_rsa")
    # Copy paste public key to Github 
    run("cat ~/.ssh/id_rsa.pub")


@task
@log_call
def pull():
    """
    Pull or clone project repo from version control
    """
    if exists(env.proj_path):
        with project():
            run("git pull")           
    else:
        with virtualenv():
            run("git clone %s project" % env.repo_url)


@task
@log_call
def install_project_requirements():
    """
    Install project requirements.txt
    """
    pip("-r %s/requirements.txt" % env.proj_path)


# @task
# @log_call
# def create_mysql_db():
#     """
#     Create MySQL database
#     """
#     pass


# @task
# @log_call
# def setup_ssl():
#     """
#     Setup SSL certificate and key
#     """
#     if not exists('/etc/nginx/ssl'):
#         sudo('mkdir /etc/nginx/ssl')
#     # create key and certificate signing request
#     with cd('/etc/nginx/ssl'):
#         # http://support.godaddy.com/help/article/3601/generating-a-certificate-signing-request-nginx
#         sudo('openssl req -new -newkey rsa:2048 -nodes ' + 
#             '-keyout %s.key -out %s.csr' % (env.proj_name, env.proj_name))
        # sudo('openssl genrsa -des3 -out %s.key 1024' % env.proj_name)
        # sudo('openssl req -new -key %s.key -out %s.csr' % 
            # (env.proj_name, env.proj_name))
    # remove passphrase
    # with cd('/etc/nginx/ssl'):
    #     sudo('cp %s.key %s.key.org' % (env.proj_name, env.proj_name))
    #     sudo('openssl rsa -in %s.key.org -out %s.key' % 
    #         (env.proj_name, env.proj_name))
    # sign cert
    # with cd('/etc/nginx/ssl'):
    #     sudo('openssl x509 -req -days 365 ' +
    #         '-in %s.csr -signkey %s.key -out %s.crt' % 
    #         (env.proj_name, env.proj_name, env.proj_name)) 



@task
@log_call
def upload_templates():
    """
    Renders & uploads all templates only if changed, reload related services
    """
    for name in get_templates():
        upload_template_and_reload(name)


@task
@log_call
def remove():
    """
    Blow away the current project.
    """
    if exists(env.venv_path):
        sudo("rm -rf %s" % env.venv_path)
    for template in get_templates().values():
        remote_path = template["remote_path"]
        if exists(remote_path):
            sudo("rm %s" % remote_path)
    # psql("DROP DATABASE IF EXISTS %s;" % env.proj_name)
    # psql("DROP USER IF EXISTS %s;" % env.proj_name)


@task
@log_call
def flushmc():
    """
    Clear memcached cache.
    """
    # todo: read location from live_settings
    sudo("echo 'flush_all' | nc 127.0.0.1 11211")


@task
@log_call
def sreload():
    """
    Reload supervisor and gunicorn processes
    """
    pid_path = "%s/gunicorn.pid" % env.proj_path
    if exists(pid_path):
        run('rm %s' % pid_path)

    args = (env.proj_name, env.proj_name)
    sudo("supervisorctl reload %s:gunicorn_%s" % args)


@task
@log_call
def update():
    """
    Pull latest from git then supervisor reload 
    """
    pull()
    flushmc()
    sreload()


@task
@log_call
def collectstatic():
    """
    Collect static assets
    """
    manage("collectstatic --noinput")


# @task
# @log_call
# def deploy():
#     """
#     Deploy latest version of the project.
#     pull latest from vcs,
#     install new requirements if any, 
#     collect any new static assets,
#     # TODO: sync and migrate the database,
#     restart gunicorn's work processes for the project.
#     """
#     # ensure global nginx conf loads first
#     upload_template_and_reload('nginx_ec2')
#     for name in get_templates():
#         upload_template_and_reload(name)
#     with project(): # /home/ubuntu/mezzanine_base/project
#         # static_dir = static()
#         with update_changed_requirements():
#             run("git pull origin master -f")
#         manage("collectstatic -v 0 --noinput")
#         # manage("syncdb --noinput")
#         # manage("migrate --noinput")
#     sreload()
#     return True


# @task
# @log_call
# def rollback():
#     """
#     Reverts project state to the last deploy.
#     When a deploy is performed, the current state of the project is
#     backed up. This includes the last commit checked out, the database,
#     and all static files. Calling rollback will revert all of these to
#     their state prior to the last deploy.
#     """
#     with project():
#         with update_changed_requirements():
#             update = "git checkout" if env.git else "hg up -C"
#             run("%s `cat last.commit`" % update)
#         with cd(join(static(), "..")):
#             run("tar -xf %s" % join(env.proj_path, "last.tar"))
#         restore("last.db")
#     supervisor_restart()

