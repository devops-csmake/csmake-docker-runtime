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
import subprocess

from CsmakeCore.CsmakeModuleAllPhase import CsmakeModuleAllPhase


class DockerRuntime(CsmakeModuleAllPhase):
    """Purpose: Execute a command inside a Docker container
       Type: Module   Library: csmake-ghactions
       Phases: *any*
       Options:
           --image      - Docker image to run (e.g. 'node:20', 'ubuntu:22.04').
                          Mutually exclusive with --dockerfile.
           --dockerfile - Path to a Dockerfile; the image is built before running.
                          Mutually exclusive with --image.
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
    """

    def default(self, options):
        image      = options.get('--image',      '').strip() or None
        dockerfile = options.get('--dockerfile', '').strip() or None
        if not image and not dockerfile:
            self.log.error("DockerRuntime requires either --image or --dockerfile")
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
                extra_volumes=None):
        """Run a Docker container synchronously.

        image       - Docker image name.  Ignored when dockerfile is set.
        env         - dict of environment variables for the container.
        host_cwd    - host path mounted at workdir inside the container.
        workdir     - working directory path inside the container.
        dockerfile  - path to a Dockerfile; image is built before running.
        entrypoint  - optional entrypoint override.
        args        - optional list of arguments after the image name.
        extra_volumes - optional list of extra 'host:container' volume strings.

        Raises RuntimeError on non-zero container exit."""
        actual_image = self._resolve_image(image, dockerfile, host_cwd)

        cmd = ['docker', 'run', '--rm',
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

    def _resolve_image(self, image, dockerfile, context):
        """Build from a Dockerfile if given; otherwise return the image name."""
        if not dockerfile:
            return image
        tag = 'csmake-docker-' + re.sub(r'[^a-z0-9]', '-',
                                         os.path.abspath(dockerfile).lower())[-40:]
        build_context = os.path.dirname(os.path.abspath(dockerfile)) or context
        subprocess.check_call(
            ['docker', 'build', '-t', tag, '-f', dockerfile, build_context])
        return tag
