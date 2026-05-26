# <copyright>
# (c) Copyright 2025 Autumn Patterson
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the
# Free Software Foundation, either version 3 of the License, or (at your
# option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU General
# Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.
# </copyright>
"""Pure-Python library for locating and invoking the Docker CLI.

Intended to be imported by other csmake modules (e.g. GHActions) rather
than used directly as a csmake section.  If you need a first-class csmake
module that runs Docker containers, use the DockerRuntime csmake module
from a future csmake-docker-runtime-modules package.
"""
import logging
import os
import re
import shutil
import subprocess

_log = logging.getLogger(__name__)

# Common installation paths checked as a last resort when docker is not on PATH.
_DOCKER_FALLBACK_PATHS = [
    '/usr/local/bin/docker',
    '/usr/bin/docker',
    '/opt/homebrew/bin/docker',                                 # Apple Silicon Homebrew
    '/usr/local/opt/docker/bin/docker',                        # Intel Homebrew
    '/Applications/Docker.app/Contents/Resources/bin/docker',  # Docker Desktop (macOS)
    '/usr/lib/docker/cli-plugins/docker',                      # Some Linux distros
]


class DockerRuntime:
    """Locate and invoke the Docker CLI to run containers.

    Usage::

        runner = DockerRuntime()                      # auto-discover docker
        runner = DockerRuntime(docker='/path/to/docker')   # explicit override
        runner.execute(image='node:20', env={}, host_cwd='/workspace')

    The docker binary is resolved in this order:
      1. ``docker`` constructor argument
      2. ``DOCKER_BIN`` environment variable (from *env*)
      3. ``docker`` on PATH
      4. Common hardcoded fallback paths (including Docker Desktop on macOS)

    Raises ``RuntimeError`` if no usable binary is found, a build step fails,
    or a container exits non-zero.
    """

    def __init__(self, docker=None):
        """
        docker - explicit path to the docker binary, or None for auto-discovery.
        """
        self._docker_override = docker

    # ------------------------------------------------------------------ #
    # Public interface                                                     #
    # ------------------------------------------------------------------ #

    def execute(self, image, env, host_cwd,
                workdir='/workspace',
                dockerfile=None,
                entrypoint=None,
                args=None,
                extra_volumes=None):
        """Run a Docker container synchronously.

        image         - Docker image name.  Ignored when *dockerfile* is set.
        env           - dict of environment variables for the container.
        host_cwd      - host path mounted at *workdir* inside the container.
        workdir       - working directory path inside the container.
        dockerfile    - path to a Dockerfile; image is built before running.
        entrypoint    - optional entrypoint override.
        args          - optional list of arguments after the image name.
        extra_volumes - optional list of extra 'host:container' volume strings.

        Raises RuntimeError on non-zero container exit.
        """
        docker_bin   = self._resolve_docker(self._docker_override, env)
        actual_image = self._resolve_image(image, dockerfile, host_cwd,
                                           docker_bin=docker_bin)

        cmd = [docker_bin, 'run', '--rm',
               '-v', '%s:%s' % (host_cwd, workdir),
               '-w', workdir]

        if entrypoint:
            cmd += ['--entrypoint', entrypoint]

        for vol in (extra_volumes or []):
            cmd += ['-v', vol]

        for k, v in (env or {}).items():
            cmd += ['-e', '%s=%s' % (k, v)]

        cmd.append(actual_image)

        if args:
            cmd.extend(args)

        rc = subprocess.call(cmd)
        if rc != 0:
            raise RuntimeError(
                "Docker container '%s' exited with code %d" % (actual_image, rc))

    # ------------------------------------------------------------------ #
    # Docker binary resolution                                             #
    # ------------------------------------------------------------------ #

    def _resolve_docker(self, override, env):
        """Return the path to a usable docker binary.

        Raises RuntimeError if nothing is found.
        """
        candidates = []

        if override:
            candidates.append(('constructor override', override))

        docker_env = (env or {}).get('DOCKER_BIN', '').strip()
        if docker_env:
            candidates.append(('DOCKER_BIN env var', docker_env))

        found = shutil.which('docker', path=(env or {}).get('PATH'))
        if found:
            candidates.append(('PATH', found))

        for path in _DOCKER_FALLBACK_PATHS:
            candidates.append(('fallback', path))

        for source, path in candidates:
            if os.path.isfile(path) and os.access(path, os.X_OK):
                _log.debug("Using docker binary from %s: %s", source, path)
                return path

        raise RuntimeError(
            "Cannot find a docker binary. Install Docker or pass "
            "docker=<path> to DockerRuntime(). "
            "Searched: %s" % ', '.join(p for _, p in candidates))

    # ------------------------------------------------------------------ #
    # Image resolution                                                     #
    # ------------------------------------------------------------------ #

    def _resolve_image(self, image, dockerfile, context, docker_bin='docker'):
        """Build from a Dockerfile if given; otherwise return the image name."""
        if not dockerfile:
            return image
        tag = 'csmake-docker-' + re.sub(r'[^a-z0-9]', '-',
                                         os.path.abspath(dockerfile).lower())[-40:]
        build_context = os.path.dirname(os.path.abspath(dockerfile)) or context
        subprocess.check_call(
            [docker_bin, 'build', '-t', tag, '-f', dockerfile, build_context])
        return tag
