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
import os
import re
import shutil
import subprocess

from CsmakeCore.CsmakeModuleAllPhase import CsmakeModuleAllPhase


# Common installation paths checked as a last resort when docker is not on PATH.
_DOCKER_FALLBACK_PATHS = [
    '/usr/local/bin/docker',
    '/usr/bin/docker',
    '/opt/homebrew/bin/docker',                                 # Apple Silicon Homebrew
    '/usr/local/opt/docker/bin/docker',                        # Intel Homebrew
    '/Applications/Docker.app/Contents/Resources/bin/docker',  # Docker Desktop (macOS)
    '/usr/lib/docker/cli-plugins/docker',                      # Some Linux distros
]


class DockerRuntime(CsmakeModuleAllPhase):
    """Purpose: Execute a command inside a Docker container
       Type: Module   Library: csmake-docker-runtime
       Phases: *any*
       Options:
           --image      - Docker image to run (e.g. 'node:20', 'ubuntu:22.04').
                          Mutually exclusive with --dockerfile.
           --dockerfile - Path to a Dockerfile; the image is built before running.
                          Mutually exclusive with --image.
           --docker     - (OPTIONAL) explicit path to the docker binary.
                          Overrides all automatic discovery.
           --entrypoint - (OPTIONAL) Override the container entrypoint.
           --args       - (OPTIONAL) Space-separated arguments appended after the
                          image name on the docker run command line.
           --workdir    - (OPTIONAL) Working directory inside the container.
                          DEFAULT: /workspace
           --volume     - (OPTIONAL) Additional volume mount(s) in host:container
                          format, comma-separated.  The current working directory
                          is always mounted at --workdir automatically.
           <key>=<value> - Environment variables forwarded to the container (all
                           keys that do NOT start with '--').
       Notes:
           The docker binary is resolved in this order:
             1. --docker option (if supplied)
             2. DOCKER_BIN environment variable
             3. 'docker' on PATH  (shutil.which)
             4. Common hardcoded paths as a last resort, including
                Docker Desktop on macOS
           The host working directory (%(WORKING)s) is always mounted at
           --workdir inside the container.  The caller is responsible for
           any protocol-specific variables (GITHUB_OUTPUT, INPUT_*, etc.)
           when calling execute() directly.
       Example:
           [DockerRuntime@cross-build]
           --image=golang:1.21
           --workdir=/app
           --args=go build ./...
           GOOS=linux
           GOARCH=amd64

           [DockerRuntime@custom-docker]
           --docker=/usr/local/bin/docker
           --image=python:3.12-slim
           --args=python -c "import sys; print(sys.version)"
    """

    def default(self, options):
        image      = options.get('--image',      '').strip() or None
        dockerfile = options.get('--dockerfile', '').strip() or None
        if not image and not dockerfile:
            self.log.error("DockerRuntime requires either --image or --dockerfile")
            self.log.failed()
            return None

        docker_override = options.get('--docker', '').strip() or None

        try:
            docker_bin = self._resolve_docker(docker_override, os.environ)
        except RuntimeError as e:
            self.log.error(str(e))
            self.log.failed()
            return None

        entrypoint = options.get('--entrypoint', '').strip() or None
        workdir    = options.get('--workdir',    '/workspace').strip()
        raw_args   = options.get('--args',       '').strip()
        args       = raw_args.split() if raw_args else None

        extra_volumes = []
        raw_volumes = options.get('--volume', '').strip()
        if raw_volumes:
            extra_volumes = [v.strip() for v in raw_volumes.split(',') if v.strip()]

        env = {k: v.strip() for k, v in options.items() if not k.startswith('--')}

        try:
            self.execute(
                image=image,
                env=env,
                host_cwd=os.getcwd(),
                workdir=workdir,
                dockerfile=dockerfile,
                entrypoint=entrypoint,
                args=args,
                extra_volumes=extra_volumes,
                docker_bin=docker_bin,
            )
            self.log.passed()
            return True
        except Exception as e:
            self.log.error("DockerRuntime failed: %s", str(e))
            self.log.failed()
            return None

    # ------------------------------------------------------------------ #
    # Python-callable interface (used by GHActions and other modules)     #
    # ------------------------------------------------------------------ #

    def execute(self, image, env, host_cwd,
                workdir='/workspace',
                dockerfile=None,
                entrypoint=None,
                args=None,
                extra_volumes=None,
                docker_bin=None):
        """Run a Docker container synchronously.

        image       - Docker image name.  Ignored when dockerfile is set.
        env         - dict of environment variables for the container.
        host_cwd    - host path mounted at workdir inside the container.
        workdir     - working directory path inside the container.
        dockerfile  - path to a Dockerfile; image is built before running.
        entrypoint  - optional entrypoint override.
        args        - optional list of arguments after the image name.
        extra_volumes - optional list of extra 'host:container' volume strings.
        docker_bin  - explicit path to the docker binary; resolved
                      automatically from the environment when omitted.

        Raises RuntimeError on non-zero container exit."""
        if docker_bin is None:
            docker_bin = self._resolve_docker(None, os.environ)

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
        """Return the path to the docker binary.

        Resolution order:
          1. override argument (from --docker option)
          2. DOCKER_BIN environment variable
          3. 'docker' on PATH
          4. Common hardcoded fallback paths (including Docker Desktop on macOS)

        Raises RuntimeError if no usable docker binary is found.
        """
        candidates = []

        if override:
            candidates.append(('--docker option', override))

        docker_env = env.get('DOCKER_BIN', '').strip()
        if docker_env:
            candidates.append(('DOCKER_BIN env var', docker_env))

        found = shutil.which('docker', path=env.get('PATH'))
        if found:
            candidates.append(('PATH', found))

        for path in _DOCKER_FALLBACK_PATHS:
            candidates.append(('fallback', path))

        for source, path in candidates:
            if os.path.isfile(path) and os.access(path, os.X_OK):
                self.log.debug("Using docker binary from %s: %s", source, path)
                return path

        raise RuntimeError(
            "Cannot find a docker binary. Install Docker or supply --docker=<path>. "
            "Searched: %s" % ', '.join(p for _, p in candidates))

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
