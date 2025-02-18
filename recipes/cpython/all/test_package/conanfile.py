from conans import AutoToolsBuildEnvironment, ConanFile, CMake, tools, RunEnvironment
from conans.errors import ConanException
from io import StringIO
import os
import re
import shutil


class CmakePython3Abi(object):
    def __init__(self, debug, pymalloc, unicode):
        self.debug, self.pymalloc, self.unicode = debug, pymalloc, unicode

    _cmake_lut = {
        None: "ANY",
        True: "ON",
        False: "OFF",
    }

    @property
    def suffix(self):
        return "{}{}{}".format(
            "d" if self.debug else "",
            "m" if self.pymalloc else "",
            "u" if self.unicode else "",
        )

    @property
    def cmake_arg(self):
        return ";".join(self._cmake_lut[a] for a in (self.debug, self.pymalloc, self.unicode))


class TestPackageConan(ConanFile):
    settings = "os", "compiler", "build_type", "arch"
    generators = "cmake"

    @property
    def _py_version(self):
        return re.match(r"^([0-9.]+)", self.deps_cpp_info["cpython"].version).group(1)

    @property
    def _pymalloc(self):
        return bool("pymalloc" in self.options["cpython"] and self.options["cpython"].pymalloc)

    @property
    def _cmake_abi(self):
        if self._py_version < tools.Version("3.8"):
            return CmakePython3Abi(
                debug=self.settings.build_type == "Debug",
                pymalloc=self._pymalloc,
                unicode=False,
            )
        else:
            return CmakePython3Abi(
                debug=self.settings.build_type == "Debug",
                pymalloc=False,
                unicode=False,
            )

    @property
    def _cmake_try_FindPythonX(self):
        if self.settings.compiler == "Visual Studio" and self.settings.build_type == "Debug":
            return False
        return True

    @property
    def _supports_modules(self):
        return self.settings.compiler != "Visual Studio" or self.options["cpython"].shared

    def build(self):
        if not tools.cross_building(self, skip_x64_x86=True):
            command = "{} --version".format(self.deps_user_info["cpython"].python)
            buffer = StringIO()
            self.run(command, output=buffer, ignore_errors=True, run_environment=True)
            self.output.info("output: %s" % buffer.getvalue())
            self.run(command, run_environment=True)

        cmake = CMake(self)
        py_major = self.deps_cpp_info["cpython"].version.split(".")[0]
        cmake.definitions["BUILD_MODULE"] = self._supports_modules
        cmake.definitions["PY_VERSION_MAJOR"] = py_major
        cmake.definitions["PY_VERSION_MAJOR_MINOR"] = ".".join(self._py_version.split(".")[:2])
        cmake.definitions["PY_FULL_VERSION"] = self.deps_cpp_info["cpython"].version
        cmake.definitions["PY_VERSION"] = self._py_version
        cmake.definitions["PY_VERSION_SUFFIX"] = self._cmake_abi.suffix
        cmake.definitions["PYTHON_EXECUTABLE"] = self.deps_user_info["cpython"].python
        cmake.definitions["USE_FINDPYTHON_X".format(py_major)] = self._cmake_try_FindPythonX
        cmake.definitions["Python{}_EXECUTABLE".format(py_major)] = self.deps_user_info["cpython"].python
        cmake.definitions["Python{}_ROOT_DIR".format(py_major)] = self.deps_cpp_info["cpython"].rootpath
        cmake.definitions["Python{}_USE_STATIC_LIBS".format(py_major)] = not self.options["cpython"].shared
        cmake.definitions["Python{}_FIND_FRAMEWORK".format(py_major)] = "NEVER"
        cmake.definitions["Python{}_FIND_REGISTRY".format(py_major)] = "NEVER"
        cmake.definitions["Python{}_FIND_IMPLEMENTATIONS".format(py_major)] = "CPython"
        cmake.definitions["Python{}_FIND_STRATEGY".format(py_major)] = "LOCATION"

        if self.settings.compiler != "Visual Studio":
            if tools.Version(self._py_version) < tools.Version("3.8"):
                cmake.definitions["Python{}_FIND_ABI".format(py_major)] = self._cmake_abi.cmake_arg

        with tools.environment_append(RunEnvironment(self).vars):
            cmake.configure()
        cmake.build()

        if not tools.cross_building(self):
            if self._supports_modules:
                with tools.vcvars(self.settings) if self.settings.compiler == "Visual Studio" else tools.no_op():
                    modsrcfolder = "py2" if tools.Version(self.deps_cpp_info["cpython"].version).major < "3" else "py3"
                    tools.mkdir(os.path.join(self.build_folder, modsrcfolder))
                    for fn in os.listdir(os.path.join(self.source_folder, modsrcfolder)):
                        shutil.copy(os.path.join(self.source_folder, modsrcfolder, fn), os.path.join(self.build_folder, modsrcfolder, fn))
                    shutil.copy(os.path.join(self.source_folder, "setup.py"), os.path.join(self.build_folder, "setup.py"))
                    env = {
                        "DISTUTILS_USE_SDK": "1",
                        "MSSdk": "1"
                    }
                    env.update(**AutoToolsBuildEnvironment(self).vars)
                    with tools.environment_append(env):
                        setup_args = [
                            "{}/setup.py".format(self.source_folder),
                            # "conan",
                            # "--install-folder", self.build_folder,
                            "build",
                            "--build-base", self.build_folder,
                            "--build-platlib", os.path.join(self.build_folder, "lib_setuptools"),
                        ]
                        if self.settings.build_type == "Debug":
                            setup_args.append("--debug")
                        self.run("{} {}".format(self.deps_user_info["cpython"].python, " ".join("\"{}\"".format(a) for a in setup_args)), run_environment=True)

    def _test_module(self, module, should_work):
        try:
            self.run("{} {}/test_package.py -b {} -t {} ".format(
                self.deps_user_info["cpython"].python, self.source_folder, self.build_folder, module), run_environment=True)
            works = True
        except ConanException as e:
            works = False
            exception = e
        if should_work == works:
            self.output.info("Result of test was expected.")
        else:
            if works:
                raise ConanException("Module '{}' works, but should not have worked".format(module))
            else:
                self.output.warn("Module '{}' does not work, but should have worked".format(module))
                raise exception

    def _cpython_option(self, name):
        try:
            return getattr(self.options["cpython"], name, False)
        except ConanException:
            return False

    def test(self):
        if not tools.cross_building(self, skip_x64_x86=True):
            self.run("{} -c \"print('hello world')\"".format(self.deps_user_info["cpython"].python), run_environment=True)

            buffer = StringIO()
            self.run("{} -c \"import sys; print('.'.join(str(s) for s in sys.version_info[:3]))\"".format(self.deps_user_info["cpython"].python), run_environment=True, output=buffer)
            self.output.info(buffer.getvalue())
            version_detected = buffer.getvalue().splitlines()[-1].strip()
            if self._py_version != version_detected:
                raise ConanException("python reported wrong version. Expected {exp}. Got {res}.".format(exp=self._py_version, res=version_detected))

            if self._supports_modules:
                self._test_module("gdbm", self._cpython_option("with_gdbm"))
                self._test_module("bz2", self._cpython_option("with_bz2"))
                self._test_module("bsddb", self._cpython_option("with_bsddb"))
                self._test_module("lzma", self._cpython_option("with_lzma"))
                self._test_module("tkinter", self._cpython_option("with_tkinter"))
                with tools.environment_append({"TERM": "ansi"}):
                    self._test_module("curses", self._cpython_option("with_curses"))

                self._test_module("expat", True)
                self._test_module("sqlite3", True)
                self._test_module("decimal", True)
                self._test_module("ctypes", True)

            if tools.is_apple_os(self.settings.os) and not self.options["cpython"].shared:
                self.output.info("Not testing the module, because these seem not to work on apple when cpython is built as a static library")
                # FIXME: find out why cpython on apple does not allow to use modules linked against a static python
            else:
                if self._supports_modules:
                    with tools.environment_append({"PYTHONPATH": [os.path.join(self.build_folder, "lib")]}):
                        self.output.info("Testing module (spam) using cmake built module")
                        self._test_module("spam", True)

                    with tools.environment_append({"PYTHONPATH": [os.path.join(self.build_folder, "lib_setuptools")]}):
                        self.output.info("Testing module (spam) using setup.py built module")
                        self._test_module("spam", True)

            # MSVC builds need PYTHONHOME set.
            with tools.environment_append({"PYTHONHOME": self.deps_user_info["cpython"].pythonhome}) if self.deps_user_info["cpython"].module_requires_pythonhome == "True" else tools.no_op():
                self.run(os.path.join("bin", "test_package"), run_environment=True)
