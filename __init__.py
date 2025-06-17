from __future__ import annotations

import json
import logging
import posixpath
import tempfile
from dataclasses import asdict
from typing import IO, TYPE_CHECKING, List

from calibre.devices.interface import DevicePlugin  # type: ignore
from calibre.devices.usbms.deviceconfig import DeviceConfig  # type: ignore

from . import rm_ssh, rm_web_interface
from .log_helper import log_args_kwargs
from .rm_data import (
    RemarkableBook,
    RemarkableBookList,
    RemarkableDeviceDescription,
    RemarkableSettings,
)

if TYPE_CHECKING:
    from calibre.ebooks.metadata.book.base import Metadata  # type: ignore

PLUGIN_NAME = "remarkable-calibre-usb-device"
print("----------------------------------- REMARKABLE PLUGIN web interface ------------------------")
device = None
logging.basicConfig(level=logging.DEBUG)
LOGGER = logging.getLogger()

RM_UUID = "#rm_uuid"


class RemarkableUsbDevice(DeviceConfig, DevicePlugin):
    VENDOR_ID = 0x04B3
    PRODUCT_ID = 0x4010

    progress = 0.0
    name = PLUGIN_NAME
    description = "Send epub and pdf files to Remarkable"
    author = "Andri Rakotomalala"
    supported_platforms = ["linux", "windows", "osx"]
    version = (0, 1, 2)  # The version number of this plugin
    minimum_calibre_version = (0, 7, 53)

    FORMATS = ["epub", "pdf"]
    CAN_SET_METADATA: list[str] = []
    MANAGES_DEVICE_PRESENCE = True
    SUPPORTS_SUB_DIRS = True
    HIDE_FORMATS_CONFIG_BOX = True
    NEWS_IN_FOLDER = True
    USER_CAN_ADD_NEW_FORMATS = False

    MUST_READ_METADATA = False
    SUPPORTS_USE_AUTHOR_SORT = False
    SAVE_TEMPLATE = "calibre/{author_sort}/{title} - {authors}"  # type: ignore

    EXTRA_CUSTOMIZATION_MESSAGE = [  # type: ignore
        # -----------
        "IP address:::" "<p>" "Use this option if you want to force the driver to listen on a " "particular IP address." "</p>",
        # -----------
        "SSH password (optional):::" "<p>Required for folders support</p>",
    ]
    EXTRA_CUSTOMIZATION_DEFAULT = [  # type: ignore
        "10.11.99.1",
        "",
    ]

    def config_widget(self):
        from calibre.gui2.device_drivers.configwidget import (  # type: ignore
            ConfigWidget,
        )

        cw = ConfigWidget(
            self.settings(),
            self.FORMATS,
            self.SUPPORTS_SUB_DIRS,
            self.MUST_READ_METADATA,
            self.SUPPORTS_USE_AUTHOR_SORT,
            self.EXTRA_CUSTOMIZATION_MESSAGE,
            self,
        )
        return cw

    @classmethod
    def settings_obj(cls):
        settings = cls.settings()
        return RemarkableSettings(*settings.extra_customization)

    @log_args_kwargs
    def startup(self):
        super().startup()

    @log_args_kwargs
    def detect_managed_devices(self, devices_on_system: List, force_refresh=False):
        global device
        settings = self.settings_obj()

        try:
            # TODO
            matching_devices = [d for d in devices_on_system if d.vendor_id == self.VENDOR_ID and d.product_id == self.PRODUCT_ID]
            # if not any(matching_devices):
            #     return
            LOGGER.info("Probably this device: %s", matching_devices)
        except:  # noqa: E722
            LOGGER.warning("USB device not detected", exc_info=True)

        try:
            if rm_web_interface.check_connection(settings.IP):
                device = RemarkableDeviceDescription(settings.IP)
                LOGGER.info(f"detected {device=}")
                return device
        except:  # noqa: E722
            LOGGER.warning("No device detected", exc_info=True)
            device = None
            return None

    @log_args_kwargs
    def debug_managed_device_detection(self, devices_on_system, output: IO):
        LOGGER.warning("----- TODO: Should write information about the devices detected on the system to output, which is a file like object.")
        return self.detect_managed_devices(devices_on_system, False)

    @log_args_kwargs
    def books(self, oncard=None, end_session=True):
        # settings = self.settings_obj()
        # return rm_web_interface.query_tree(settings.IP, "").ls_recursive()
        booklists = (RemarkableBookList(), None, None)
        booklist0, _, _ = self.sync_booklists(booklists)
        return booklist0

    def _create_upload_path(self, mdata, fname):
        from calibre.devices.utils import create_upload_path  # type: ignore
        from calibre.utils.filenames import ascii_filename as sanitize  # type: ignore

        return create_upload_path(
            mdata,
            fname,
            self.save_template(),
            sanitize,
            prefix_path="",
            path_type=posixpath,
            maxlen=250,
            use_subdirs="/" in self.save_template(),
            news_in_folder=self.NEWS_IN_FOLDER,
        )

    @log_args_kwargs
    def upload_books(self, files_original, names, on_card=None, end_session=True, metadata: list[Metadata] = None):
        locations = []
        self.progress = 0.0
        settings = self.settings_obj()

        if not metadata:
            metadata = [None] * len(files_original)
        step = 100 / len(files_original)
        has_ssh = rm_ssh.test_connection(settings)
        existing_folders = rm_web_interface.query_tree(settings.IP, "").ls_dir_recursive_dict() if has_ssh else {}
        needs_reboot = False
        for local_path, visible_name, m in zip(files_original, names, metadata):
            folder_id = ""
            folder_id_final = ""
            is_new_folder = False
            upload_path = self._create_upload_path(m, visible_name)
            if has_ssh:
                if upload_path:
                    parts = upload_path.split("/")
                    parts = parts[:-1]
                    parent_folder_id = ""
                    for i in range(len(parts)):
                        part_full = "/".join(parts[: i + 1])
                        LOGGER.debug(
                            f"Looking if {part_full=} already exists on remarkable",
                        )
                        # FIXME: does not work when folder is new, and uploading multiple files at the same time
                        folder_id_final = existing_folders.get(part_full)
                        LOGGER.debug(f"{folder_id_final=}")
                        if not folder_id_final:
                            part_name = parts[i]
                            folder_id_final = rm_ssh.mkdir(settings, part_name, parent_folder_id)
                            existing_folders[part_full] = folder_id_final
                            needs_reboot = True
                            LOGGER.debug(f"after mkdir {folder_id_final=}")
                            is_new_folder = True
                        parent_folder_id = folder_id_final
            locations.append(upload_path)

            # FIXME: fails when author has special character ';'
            rm_web_interface.upload_file(settings.IP, local_path, folder_id_final, visible_name)

            if has_ssh:
                file_uuid = rm_ssh.get_latest_upload_uuid(settings)
                m.set_user_metadata(RM_UUID, {"#value#": file_uuid, "datatype": "text"})
                rm_ssh.sed(settings, f"{file_uuid}.metadata", '"parent": ""', f'"parent": "{folder_id_final}"')
                if is_new_folder:
                    needs_reboot = True

            self.progress += step

        if needs_reboot and has_ssh:
            rm_ssh.xochitl_restart_after(settings, 5.0)
        self.progress = 100.0

        return (locations, metadata, None)

    @log_args_kwargs
    def open(self, connected_device, library_uuid):
        pass

    @log_args_kwargs
    def is_usb_connected(self, devices_on_system, debug=False, only_presence=False):
        global device
        return True, device

    @log_args_kwargs
    def eject(self):
        global device
        device = None

    @log_args_kwargs
    def get_device_information(self, end_session=True):
        global device
        if device is not None:
            return (str(device), 1, 1, "application/epub")

    @log_args_kwargs
    def total_space(self, end_session=True):
        return 999999999, -1, -1

    @log_args_kwargs
    def get_driveinfo(self):
        return super().get_driveinfo()

    @log_args_kwargs
    def get_device_uid(self):
        return device.random_id

    @log_args_kwargs
    def get_file(self, path, outfile, end_session=True):
        return super().get_file(path, outfile, end_session)

    @log_args_kwargs
    def get_option(self, opt_string, default=None):
        return super().get_option(opt_string, default)

    @log_args_kwargs
    def get_user_blacklisted_devices(self):
        return super().get_user_blacklisted_devices()

    @log_args_kwargs
    def set_driveinfo_name(self, location_code, name):
        return super().set_driveinfo_name(location_code, name)

    @log_args_kwargs
    def set_library_info(self, library_name, library_uuid, field_metadata):
        return super().set_library_info(library_name, library_uuid, field_metadata)

    @log_args_kwargs
    def set_option(self, opt_string, opt_value):
        return super().set_option(opt_string, opt_value)

    @log_args_kwargs
    def set_plugboards(self, plugboards, pb_func):
        return super().set_plugboards(plugboards, pb_func)

    @log_args_kwargs
    def set_progress_reporter(self, report_progress):
        def dummy_set_progress_reporter(*args, **kwargs):
            return int(self.progress)

        return dummy_set_progress_reporter

    @log_args_kwargs
    def set_user_blacklisted_devices(self, devices):
        return super().set_user_blacklisted_devices(devices)

    @log_args_kwargs
    def shutdown(self):
        return super().shutdown()

    @log_args_kwargs
    def synchronize_with_db(self, db, book_id, book_metadata, first_call):
        return super().synchronize_with_db(db, book_id, book_metadata, first_call)

    @log_args_kwargs
    def free_space(self, end_session=True):
        return 999999999, -1, -1

    @log_args_kwargs
    def temporary_file(self, suffix):
        return super().temporary_file(suffix)

    @log_args_kwargs
    def test_bcd(self, bcdDevice, bcd):
        return super().test_bcd(bcdDevice, bcd)

    @log_args_kwargs
    def specialize_global_preferences(self, device_prefs):
        return super().specialize_global_preferences(device_prefs)

    @log_args_kwargs
    def start_plugin(self):
        return super().start_plugin()

    @log_args_kwargs
    def stop_plugin(self):
        return super().stop_plugin()

    @log_args_kwargs
    def sync_booklists(self, booklists: tuple[RemarkableBookList, list, list], end_session=True):
        settings = self.settings_obj()
        if not rm_ssh.test_connection(settings) or booklists is None:
            # TODO use rm_web_interface if ssh is not available
            return RemarkableBookList(), None, None

        booklist0, _, _ = booklists
        tree = rm_web_interface.query_tree(settings.IP, "")
        existing_docs = tree.ls_recursive() + tree.ls_uuid()
        LOGGER.debug(f"{existing_docs=}")
        LOGGER.info("Attempting to open existing calibre metadata on device")
        bookslist = self.load_booklist(settings)
        if existing_docs:
            booklist_on_device = [b for b in bookslist if b.path in existing_docs or b.rm_uuid in existing_docs]
        else:
            booklist_on_device = bookslist
        LOGGER.info("got booklist_on_device=%s", booklist_on_device)

        # TOOD optimize this, maybe somehow hash RemarkableBookList
        for book in booklist0:
            if book not in booklist_on_device:
                booklist_on_device.append(book)

        with tempfile.NamedTemporaryFile("w+t", delete=False) as fp:
            content = json.dumps([asdict(x) for x in booklist_on_device], indent=1, sort_keys=True, default=str)
            fp.write(content)
            fp.flush()
            rm_ssh.scp(settings, fp.name, settings.CALIBRE_METADATA_PATH)

        LOGGER.info("booklist_on_device=%s", booklist_on_device)
        LOGGER.info("booklist0=%s", booklist0)
        # Make sure our local booklist matches what's on the device too
        for book in booklist_on_device:
            if book not in booklist0:
                LOGGER.info("Adding book %s", book)
                booklist0.add_book(book)

        return booklist0, None, None

    def load_booklist(self, settings: RemarkableSettings):
        json_dict = json.loads(rm_ssh.cat(settings, settings.CALIBRE_METADATA_PATH)) or []
        return list(map(lambda x: RemarkableBook(**x), json_dict))

    @log_args_kwargs
    def prepare_addable_books(self, paths):
        return super().prepare_addable_books(paths)

    @log_args_kwargs
    def delete_books(self, paths, end_session=True):
        """
        Delete books at paths on device.
        """
        settings = self.settings_obj()
        has_ssh = rm_ssh.test_connection(settings)
        if not has_ssh:
            raise SystemError("This feature requires SSH")

        # we assume the path generated in create_upload_path is unique
        LOGGER.debug(f"{paths=}")
        paths = [b.rm_uuid for b in self.load_booklist(settings) if b.path in paths]
        rm_ssh.rm(settings, paths=" ".join(f"{u}*" for u in paths))

        rm_ssh.xochitl_restart_after(settings, 5.0)

    @classmethod
    def remove_books_from_metadata(cls, paths, booklists):
        booklist0: RemarkableBookList = booklists[0]
        to_remove = []
        for book in booklist0:
            if book.path in paths:
                to_remove.append(book)

        for book in to_remove:
            booklist0.remove_book(book)

    @log_args_kwargs
    def do_user_config(self, parent=None):
        return super().do_user_config(parent)

    @log_args_kwargs
    def can_handle(self, device_info, debug=False):
        return super().can_handle(device_info, debug)

    @log_args_kwargs
    def can_handle_windows(self, usbdevice, debug=False):
        return super().can_handle_windows(usbdevice, debug)

    @log_args_kwargs
    def card_prefix(self, end_session=True):
        return None, None

    @log_args_kwargs
    def cli_main(self, args):
        return super().cli_main(args)

    @log_args_kwargs
    def customization_help(self, gui=False):
        return super().customization_help(gui)

    @log_args_kwargs
    def is_customizable(self):
        return super().is_customizable()

    @log_args_kwargs
    def ignore_connected_device(self, uid):
        pass

    @log_args_kwargs
    def post_yank_cleanup(self):
        return super().post_yank_cleanup()

    @log_args_kwargs
    def is_dynamically_controllable(self):
        return super().is_dynamically_controllable()

    @classmethod
    @log_args_kwargs
    def add_books_to_metadata(
        cls,
        locations: tuple[list[str], list, list],
        metadata: list[Metadata],
        booklists: tuple[RemarkableBookList, RemarkableBookList, RemarkableBookList],
    ):
        settings = cls.settings_obj()
        if not rm_ssh.test_connection(settings):
            return

        booklist0, _, _ = booklists
        LOGGER.info(f"Adding books to metadata, locations: {locations}, metadata: {metadata}, booklists: {booklists}")
        for i, m in enumerate(metadata):
            title: str = m.get("title")  # type: ignore
            authors: list[str] = m.get("authors")  # type: ignore
            tags: list[str] = m.get("tags")  # type: ignore
            pubdate = m.get("pubdate").timetuple()
            size = m.get("size")
            uuid: str = m.get("uuid")  # type: ignore
            rm_uuid = m.get(RM_UUID)
            path = locations[0][i]
            b = RemarkableBook(
                title=title,
                uuid=uuid,
                rm_uuid=rm_uuid,
                authors=authors,
                size=size,
                datetime=pubdate,
                tags=tags,
                path=path,
            )
            if b not in booklist0:
                booklist0.add_book(b, None)
