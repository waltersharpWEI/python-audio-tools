# Audio Tools, a module and set of tools for manipulating audio data
# Copyright (C) 2007-2016  Brian Langenberger

# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.

# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.

# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301  USA


from audiotools import (AudioFile, MetaData, InvalidFile, Image,
                        WaveContainer, AiffContainer,
                        Sheet, SheetTrack, SheetIndex)
from audiotools.vorbiscomment import VorbisComment


# the maximum padding size to use when rewriting metadata blocks
MAX_PADDING_SIZE = 2 ** 20


class InvalidFLAC(InvalidFile):
    pass


class FlacMetaDataBlockTooLarge(Exception):
    """raised if one attempts to build a FlacMetaDataBlock too large"""

    pass


class FlacMetaData(MetaData):
    """a class for managing a native FLAC's metadata"""

    def __init__(self, blocks):
        MetaData.__setattr__(self, "block_list", list(blocks))

    def has_block(self, block_id):
        """returns True if the given block ID is present"""

        return block_id in (b.BLOCK_ID for b in self.block_list)

    def add_block(self, block):
        """adds the given block to our list of blocks"""

        # the specification only requires that STREAMINFO be first
        # the rest are largely arbitrary,
        # though I like to keep PADDING as the last block for aesthetic reasons
        PREFERRED_ORDER = [Flac_STREAMINFO.BLOCK_ID,
                           Flac_SEEKTABLE.BLOCK_ID,
                           Flac_CUESHEET.BLOCK_ID,
                           Flac_VORBISCOMMENT.BLOCK_ID,
                           Flac_PICTURE.BLOCK_ID,
                           Flac_APPLICATION.BLOCK_ID,
                           Flac_PADDING.BLOCK_ID]

        stop_blocks = set(
            PREFERRED_ORDER[PREFERRED_ORDER.index(block.BLOCK_ID) + 1:])

        for (index, old_block) in enumerate(self.block_list):
            if old_block.BLOCK_ID in stop_blocks:
                self.block_list.insert(index, block)
                break
        else:
            self.block_list.append(block)

    def get_block(self, block_id):
        """returns the first instance of the given block_id

        may raise IndexError if the block is not in our list of blocks"""

        for block in self.block_list:
            if block.BLOCK_ID == block_id:
                return block
        else:
            raise IndexError()

    def get_blocks(self, block_id):
        """returns all instances of the given block_id in our list of blocks"""

        return [b for b in self.block_list if (b.BLOCK_ID == block_id)]

    def replace_blocks(self, block_id, blocks):
        """replaces all instances of the given block_id with
        blocks taken from the given list

        if insufficient matching blocks are present,
        this uses add_block() to populate the remainder

        if additional matching blocks are present,
        they are removed
        """

        new_blocks = []

        for block in self.block_list:
            if block.BLOCK_ID == block_id:
                if len(blocks) > 0:
                    new_blocks.append(blocks.pop(0))
                else:
                    pass
            else:
                new_blocks.append(block)

        self.block_list = new_blocks

        while len(blocks) > 0:
            self.add_block(blocks.pop(0))

    def __setattr__(self, attr, value):
        if attr in self.FIELDS:
            try:
                vorbis_comment = self.get_block(Flac_VORBISCOMMENT.BLOCK_ID)
            except IndexError:
                # add VORBIS comment block if necessary
                from audiotools import VERSION

                vorbis_comment = Flac_VORBISCOMMENT(
                    [], u"Python Audio Tools {}".format(VERSION))

                self.add_block(vorbis_comment)

            setattr(vorbis_comment, attr, value)
        else:
            MetaData.__setattr__(self, attr, value)

    def __getattr__(self, attr):
        if attr in self.FIELDS:
            try:
                return getattr(self.get_block(Flac_VORBISCOMMENT.BLOCK_ID),
                               attr)
            except IndexError:
                # no VORBIS comment block, so all values are None
                return None
        else:
            return MetaData.__getattribute__(self, attr)

    def __delattr__(self, attr):
        if attr in self.FIELDS:
            try:
                delattr(self.get_block(Flac_VORBISCOMMENT.BLOCK_ID), attr)
            except IndexError:
                # no VORBIS comment block, so nothing to delete
                pass
        else:
            MetaData.__delattr__(self, attr)

    @classmethod
    def converted(cls, metadata):
        """takes a MetaData object and returns a FlacMetaData object"""

        if metadata is None:
            return None
        elif isinstance(metadata, FlacMetaData):
            return cls([block.copy() for block in metadata.block_list])
        else:
            return cls([Flac_VORBISCOMMENT.converted(metadata)] +
                       [Flac_PICTURE.converted(image)
                        for image in metadata.images()] +
                       [Flac_PADDING(4096)])

    def add_image(self, image):
        """embeds an Image object in this metadata"""

        self.add_block(Flac_PICTURE.converted(image))

    def delete_image(self, image):
        """deletes an image object from this metadata"""

        self.block_list = [b for b in self.block_list
                           if not ((b.BLOCK_ID == Flac_PICTURE.BLOCK_ID) and
                                   (b == image))]

    def images(self):
        """returns a list of embedded Image objects"""

        return self.get_blocks(Flac_PICTURE.BLOCK_ID)

    @classmethod
    def supports_images(cls):
        """returns True"""

        return True

    def clean(self):
        """returns (FlacMetaData, [fixes]) tuple

        where FlacMetaData is a new MetaData object fixed of problems
        and fixes is a list of Unicode strings of fixes performed
        """

        from audiotools.text import (CLEAN_FLAC_REORDERED_STREAMINFO,
                                     CLEAN_FLAC_MULITPLE_STREAMINFO,
                                     CLEAN_FLAC_MULTIPLE_VORBISCOMMENT,
                                     CLEAN_FLAC_MULTIPLE_SEEKTABLE,
                                     CLEAN_FLAC_MULTIPLE_CUESHEET,
                                     CLEAN_FLAC_UNDEFINED_BLOCK)

        fixes_performed = []
        cleaned_blocks = []

        for block in self.block_list:
            if block.BLOCK_ID == Flac_STREAMINFO.BLOCK_ID:
                # reorder STREAMINFO block to be first, if necessary
                if len(cleaned_blocks) == 0:
                    cleaned_blocks.append(block)
                elif cleaned_blocks[0].BLOCK_ID != block.BLOCK_ID:
                    fixes_performed.append(
                        CLEAN_FLAC_REORDERED_STREAMINFO)
                    cleaned_blocks.insert(0, block)
                else:
                    fixes_performed.append(
                        CLEAN_FLAC_MULITPLE_STREAMINFO)
            elif block.BLOCK_ID == Flac_VORBISCOMMENT.BLOCK_ID:
                if block.BLOCK_ID in [b.BLOCK_ID for b in cleaned_blocks]:
                    # remove redundant VORBIS_COMMENT blocks
                    fixes_performed.append(
                        CLEAN_FLAC_MULTIPLE_VORBISCOMMENT)
                else:
                    # recursively clean up the text fields in FlacVorbisComment
                    (block, block_fixes) = block.clean()
                    cleaned_blocks.append(block)
                    fixes_performed.extend(block_fixes)
            elif block.BLOCK_ID == Flac_PICTURE.BLOCK_ID:
                # recursively clean up any image blocks
                (block, block_fixes) = block.clean()
                cleaned_blocks.append(block)
                fixes_performed.extend(block_fixes)
            elif block.BLOCK_ID == Flac_APPLICATION.BLOCK_ID:
                cleaned_blocks.append(block)
            elif block.BLOCK_ID == Flac_SEEKTABLE.BLOCK_ID:
                # remove redundant seektable, if necessary
                if block.BLOCK_ID in [b.BLOCK_ID for b in cleaned_blocks]:
                    fixes_performed.append(
                        CLEAN_FLAC_MULTIPLE_SEEKTABLE)
                else:
                    (block, block_fixes) = block.clean()
                    cleaned_blocks.append(block)
                    fixes_performed.extend(block_fixes)
            elif block.BLOCK_ID == Flac_CUESHEET.BLOCK_ID:
                # remove redundant cuesheet, if necessary
                if block.BLOCK_ID in [b.BLOCK_ID for b in cleaned_blocks]:
                    fixes_performed.append(
                        CLEAN_FLAC_MULTIPLE_CUESHEET)
                else:
                    cleaned_blocks.append(block)
            elif block.BLOCK_ID == Flac_PADDING.BLOCK_ID:
                cleaned_blocks.append(block)
            else:
                # remove undefined blocks
                fixes_performed.append(CLEAN_FLAC_UNDEFINED_BLOCK)

        return (self.__class__(cleaned_blocks), fixes_performed)

    def __repr__(self):
        return "FlacMetaData({!r})".format(self.block_list)

    def intersection(self, metadata):
        """given a MetaData-compatible object,
        returns a new MetaData object which contains
        all the matching fields and images of this object and 'metadata'
        """

        def block_present(block):
            for other_block in metadata.get_blocks(block.BLOCK_ID):
                if block == other_block:
                    return True
            else:
                return False

        if type(metadata) is FlacMetaData:
            blocks = []

            for block in self.block_list:
                if ((block.BLOCK_ID == Flac_VORBISCOMMENT.BLOCK_ID) and
                    metadata.has_block(block.BLOCK_ID)):
                    # merge VORBIS blocks seperately, if present
                    blocks.append(
                        block.intersection(metadata.get_block(block.BLOCK_ID)))
                elif block_present(block):
                    blocks.append(block.copy())

            return FlacMetaData(blocks)
        else:
            return MetaData.intersection(self, metadata)

    @classmethod
    def parse(cls, reader):
        """returns a FlacMetaData object from the given BitstreamReader
        which has already parsed the 4-byte 'fLaC' file ID"""

        block_list = []

        last = 0

        while last != 1:
            (last, block_type, block_length) = reader.parse("1u7u24u")

            if block_type == 0:    # STREAMINFO
                block_list.append(
                    Flac_STREAMINFO.parse(reader))
            elif block_type == 1:  # PADDING
                block_list.append(
                    Flac_PADDING.parse(reader, block_length))
            elif block_type == 2:  # APPLICATION
                block_list.append(
                    Flac_APPLICATION.parse(reader, block_length))
            elif block_type == 3:  # SEEKTABLE
                block_list.append(
                    Flac_SEEKTABLE.parse(reader, block_length // 18))
            elif block_type == 4:  # VORBIS_COMMENT
                block_list.append(
                    Flac_VORBISCOMMENT.parse(reader))
            elif block_type == 5:  # CUESHEET
                block_list.append(
                    Flac_CUESHEET.parse(reader))
            elif block_type == 6:  # PICTURE
                block_list.append(
                    Flac_PICTURE.parse(reader))
            elif (block_type >= 7) and (block_type <= 126):
                from audiotools.text import ERR_FLAC_RESERVED_BLOCK
                raise ValueError(ERR_FLAC_RESERVED_BLOCK.format(block_type))
            else:
                from audiotools.text import ERR_FLAC_INVALID_BLOCK
                raise ValueError(ERR_FLAC_INVALID_BLOCK)

        return cls(block_list)

    def raw_info(self):
        """returns human-readable metadata as a unicode string"""

        from os import linesep

        return linesep.join(
            [u"FLAC Tags:"] + [block.raw_info() for block in self.blocks()])

    def blocks(self):
        """yields FlacMetaData's individual metadata blocks"""

        for block in self.block_list:
            yield block

    def build(self, writer):
        """writes the FlacMetaData to the given BitstreamWriter
        not including the 4-byte 'fLaC' file ID"""

        from audiotools import iter_last

        for (last_block,
             block) in iter_last(iter([b for b in self.blocks()
                                       if (b.size() < (2 ** 24))])):
            if not last_block:
                writer.build("1u7u24u", (0, block.BLOCK_ID, block.size()))
            else:
                writer.build("1u7u24u", (1, block.BLOCK_ID, block.size()))

            block.build(writer)

    def size(self):
        """returns the size of all metadata blocks
        including the block headers
        but not including the 4-byte 'fLaC' file ID"""

        return sum(4 + b.size() for b in self.block_list)


class Flac_STREAMINFO(object):
    BLOCK_ID = 0

    def __init__(self, minimum_block_size, maximum_block_size,
                 minimum_frame_size, maximum_frame_size,
                 sample_rate, channels, bits_per_sample,
                 total_samples, md5sum):
        """all values are non-negative integers except for md5sum
        which is a 16-byte binary string"""

        self.minimum_block_size = minimum_block_size
        self.maximum_block_size = maximum_block_size
        self.minimum_frame_size = minimum_frame_size
        self.maximum_frame_size = maximum_frame_size
        self.sample_rate = sample_rate
        self.channels = channels
        self.bits_per_sample = bits_per_sample
        self.total_samples = total_samples
        self.md5sum = md5sum

    def copy(self):
        """returns a duplicate of this metadata block"""

        return Flac_STREAMINFO(self.minimum_block_size,
                               self.maximum_block_size,
                               self.minimum_frame_size,
                               self.maximum_frame_size,
                               self.sample_rate,
                               self.channels,
                               self.bits_per_sample,
                               self.total_samples,
                               self.md5sum)

    def __eq__(self, block):
        for attr in ["minimum_block_size",
                     "maximum_block_size",
                     "minimum_frame_size",
                     "maximum_frame_size",
                     "sample_rate",
                     "channels",
                     "bits_per_sample",
                     "total_samples",
                     "md5sum"]:
            if ((not hasattr(block, attr)) or (getattr(self, attr) !=
                                               getattr(block, attr))):
                return False
        else:
            return True

    def __repr__(self):
        return "Flac_STREAMINFO({})".format(",".join(
            ["{}={!r}".format(key, getattr(self, key))
             for key in ["minimum_block_size",
                         "maximum_block_size",
                         "minimum_frame_size",
                         "maximum_frame_size",
                         "sample_rate",
                         "channels",
                         "bits_per_sample",
                         "total_samples",
                         "md5sum"]]))

    def raw_info(self):
        """returns a human-readable version of this metadata block
        as unicode"""

        from audiotools import hex_string
        from os import linesep

        return linesep.join(
            [u"  STREAMINFO:",
             u"    minimum block size = {:d}".format(self.minimum_block_size),
             u"    maximum block size = {:d}".format(self.maximum_block_size),
             u"    minimum frame size = {:d}".format(self.minimum_frame_size),
             u"    maximum frame size = {:d}".format(self.maximum_frame_size),
             u"           sample rate = {:d}".format(self.sample_rate),
             u"              channels = {:d}".format(self.channels),
             u"       bits-per-sample = {:d}".format(self.bits_per_sample),
             u"         total samples = {:d}".format(self.total_samples),
             u"               MD5 sum = {}".format(hex_string(self.md5sum))])

    @classmethod
    def parse(cls, reader):
        """returns this metadata block from a BitstreamReader"""

        values = reader.parse("16u16u24u24u20u3u5u36U16b")
        values[5] += 1  # channels
        values[6] += 1  # bits-per-sample
        return cls(*values)

    def build(self, writer):
        """writes this metadata block to a BitstreamWriter"""

        writer.build("16u16u24u24u20u3u5u36U16b",
                     (self.minimum_block_size,
                      self.maximum_block_size,
                      self.minimum_frame_size,
                      self.maximum_frame_size,
                      self.sample_rate,
                      self.channels - 1,
                      self.bits_per_sample - 1,
                      self.total_samples,
                      self.md5sum))

    def size(self):
        """the size of this metadata block
        not including the 4-byte block header"""

        return 34


class Flac_PADDING(object):
    BLOCK_ID = 1

    def __init__(self, length):
        self.length = length

    def copy(self):
        """returns a duplicate of this metadata block"""

        return Flac_PADDING(self.length)

    def __eq__(self, block):
        if hasattr(block, "length"):
            return self.length == block.length
        else:
            return False

    def __repr__(self):
        return "Flac_PADDING({!r})".format(self.length)

    def raw_info(self):
        """returns a human-readable version of this metadata block
        as unicode"""

        from os import linesep

        return linesep.join(
            [u"  PADDING:",
             u"    length = {:d}".format(self.length)])

    @classmethod
    def parse(cls, reader, block_length):
        """returns this metadata block from a BitstreamReader"""

        reader.skip_bytes(block_length)
        return cls(length=block_length)

    def build(self, writer):
        """writes this metadata block to a BitstreamWriter"""

        writer.write_bytes(b"\x00" * self.length)

    def size(self):
        """the size of this metadata block
        not including the 4-byte block header"""

        return self.length


class Flac_APPLICATION(object):
    BLOCK_ID = 2

    def __init__(self, application_id, data):
        self.application_id = application_id
        self.data = data

    def __eq__(self, block):
        for attr in ["application_id", "data"]:
            if ((not hasattr(block, attr)) or (getattr(self, attr) !=
                                               getattr(block, attr))):
                return False
        else:
            return True

    def copy(self):
        """returns a duplicate of this metadata block"""

        return Flac_APPLICATION(self.application_id,
                                self.data)

    def __repr__(self):
        return "Flac_APPLICATION({!r}, {!r})".format(
            self.application_id, self.data)

    def raw_info(self):
        """returns a human-readable version of this metadata block
        as unicode"""

        from os import linesep

        return u"  APPLICATION:{}    {} ({:d} bytes)".format(
            linesep,
            self.application_id.decode('ascii'),
            len(self.data))

    @classmethod
    def parse(cls, reader, block_length):
        """returns this metadata block from a BitstreamReader"""

        return cls(application_id=reader.read_bytes(4),
                   data=reader.read_bytes(block_length - 4))

    def build(self, writer):
        """writes this metadata block to a BitstreamWriter"""

        writer.write_bytes(self.application_id)
        writer.write_bytes(self.data)

    def size(self):
        """the size of this metadata block
        not including the 4-byte block header"""

        return len(self.application_id) + len(self.data)


class Flac_SEEKTABLE(object):
    BLOCK_ID = 3

    def __init__(self, seekpoints):
        """seekpoints is a list of
        (PCM frame offset, byte offset, PCM frame count) tuples"""
        self.seekpoints = seekpoints

    def __eq__(self, block):
        if hasattr(block, "seekpoints"):
            return self.seekpoints == block.seekpoints
        else:
            return False

    def copy(self):
        """returns a duplicate of this metadata block"""

        return Flac_SEEKTABLE(self.seekpoints[:])

    def __repr__(self):
        return "Flac_SEEKTABLE({!r})".format(self.seekpoints)

    def raw_info(self):
        """returns a human-readable version of this metadata block
        as unicode"""

        from os import linesep

        return linesep.join(
            [u"  SEEKTABLE:",
             u"    first sample   file offset   frame samples"] +
            [u"  {:14d} {:13X} {:15d}".format(seekpoint[0],
                                              seekpoint[1],
                                              seekpoint[2])
             for seekpoint in self.seekpoints])

    @classmethod
    def parse(cls, reader, total_seekpoints):
        """returns this metadata block from a BitstreamReader"""

        return cls([tuple(reader.parse("64U64U16u"))
                    for i in range(total_seekpoints)])

    def build(self, writer):
        """writes this metadata block to a BitstreamWriter"""

        for seekpoint in self.seekpoints:
            writer.build("64U64U16u", seekpoint)

    def size(self):
        """the size of this metadata block
        not including the 4-byte block header"""

        from audiotools.bitstream import format_size

        return (format_size("64U64U16u") // 8) * len(self.seekpoints)

    def clean(self):
        """removes any empty seek points
        and ensures PCM frame offset and byte offset
        are both incrementing"""

        fixes_performed = []
        nonempty_points = [seekpoint for seekpoint in self.seekpoints
                           if (seekpoint[2] != 0)]

        if len(nonempty_points) != len(self.seekpoints):
            from audiotools.text import CLEAN_FLAC_REMOVE_SEEKPOINTS
            fixes_performed.append(CLEAN_FLAC_REMOVE_SEEKPOINTS)

        ascending_order = list(set(nonempty_points))
        ascending_order.sort()

        if ascending_order != nonempty_points:
            from audiotools.text import CLEAN_FLAC_REORDER_SEEKPOINTS
            fixes_performed.append(CLEAN_FLAC_REORDER_SEEKPOINTS)

        return (Flac_SEEKTABLE(ascending_order), fixes_performed)


class Flac_VORBISCOMMENT(VorbisComment):
    BLOCK_ID = 4

    def copy(self):
        """returns a duplicate of this metadata block"""

        return Flac_VORBISCOMMENT(self.comment_strings[:],
                                  self.vendor_string)

    def __repr__(self):
        return "Flac_VORBISCOMMENT({!r}, {!r})".format(
            self.comment_strings, self.vendor_string)

    def raw_info(self):
        """returns a human-readable version of this metadata block
        as unicode"""

        from os import linesep
        from audiotools import output_table

        # align the text strings on the "=" sign, if any

        table = output_table()

        for comment in self.comment_strings:
            row = table.row()
            row.add_column(u" " * 4)
            if u"=" in comment:
                (tag, value) = comment.split(u"=", 1)
                row.add_column(tag, "right")
                row.add_column(u"=")
                row.add_column(value)
            else:
                row.add_column(comment)
                row.add_column(u"")
                row.add_column(u"")

        return (u"  VORBIS_COMMENT:" + linesep +
                u"    {}".format(self.vendor_string) +
                linesep +
                linesep.join(table.format()))

    @classmethod
    def converted(cls, metadata):
        """converts a MetaData object to a Flac_VORBISCOMMENT object"""

        if (metadata is None) or (isinstance(metadata, Flac_VORBISCOMMENT)):
            return metadata
        else:
            # make VorbisComment do all the work,
            # then lift its data into a new Flac_VORBISCOMMENT
            metadata = VorbisComment.converted(metadata)
            return cls(metadata.comment_strings,
                       metadata.vendor_string)

    @classmethod
    def parse(cls, reader):
        """returns this metadata block from a BitstreamReader"""

        reader.set_endianness(True)
        try:
            vendor_string = \
                reader.read_bytes(reader.read(32)).decode('utf-8', 'replace')

            return cls([reader.read_bytes(reader.read(32)).decode('utf-8',
                                                                  'replace')
                        for i in range(reader.read(32))],
                       vendor_string)
        finally:
            reader.set_endianness(False)

    def build(self, writer):
        """writes this metadata block to a BitstreamWriter"""

        writer.set_endianness(True)
        try:
            vendor_string = self.vendor_string.encode('utf-8')
            writer.write(32, len(vendor_string))
            writer.write_bytes(vendor_string)
            writer.write(32, len(self.comment_strings))
            for comment_string in self.comment_strings:
                comment_string = comment_string.encode('utf-8')
                writer.write(32, len(comment_string))
                writer.write_bytes(comment_string)
        finally:
            writer.set_endianness(False)

    def size(self):
        """the size of this metadata block
        not including the 4-byte block header"""

        return (4 + len(self.vendor_string.encode('utf-8')) +
                4 +
                sum(4 + len(comment.encode('utf-8'))
                    for comment in self.comment_strings))


class Flac_CUESHEET(Sheet):
    BLOCK_ID = 5

    def __init__(self, catalog_number, lead_in_samples, is_cdda, tracks):
        """catalog_number is a 128 byte ASCII string, padded with NULLs
        lead_in_samples is typically 2 seconds of samples
        is_cdda is 1 if audio if from CDDA, 0 otherwise
        tracks is a list of Flac_CHESHEET_track objects"""

        assert(isinstance(catalog_number, bytes))
        assert(isinstance(lead_in_samples, int) or
               isinstance(lead_in_samples, long))
        assert(is_cdda in {1, 0})

        self.__catalog_number__ = catalog_number
        self.__lead_in_samples__ = lead_in_samples
        self.__is_cdda__ = is_cdda
        self.__tracks__ = tracks

    def copy(self):
        """returns a duplicate of this metadata block"""

        return Flac_CUESHEET(self.__catalog_number__,
                             self.__lead_in_samples__,
                             self.__is_cdda__,
                             [track.copy() for track in self.__tracks__])

    def __eq__(self, cuesheet):
        if isinstance(cuesheet, Flac_CUESHEET):
            return ((self.__catalog_number__ ==
                     cuesheet.__catalog_number__) and
                    (self.__lead_in_samples__ ==
                     cuesheet.__lead_in_samples__) and
                    (self.__is_cdda__ == cuesheet.__is_cdda__) and
                    (self.__tracks__ == cuesheet.__tracks__))
        else:
            return Sheet.__eq__(self, cuesheet)

    def __repr__(self):
        return "Flac_CUESHEET({})".format(",".join(
            ["{}={!r}".format(key, getattr(self, "__" + key + "__"))
             for key in ["catalog_number",
                         "lead_in_samples",
                         "is_cdda",
                         "tracks"]]))

    def raw_info(self):
        """returns a human-readable version of this metadata block
        as unicode"""

        from os import linesep

        return linesep.join(
            [u"  CUESHEET:",
             u"     catalog number = {}".format(
                 self.__catalog_number__.decode('ascii', 'replace')),
             u"    lead-in samples = {:d}".format(self.__lead_in_samples__),
             u"            is CDDA = {:d}".format(self.__is_cdda__)] +
            [track.raw_info(4) for track in self.__tracks__])

    @classmethod
    def parse(cls, reader):
        """returns this metadata block from a BitstreamReader"""

        (catalog_number,
         lead_in_samples,
         is_cdda,
         track_count) = reader.parse("128b64U1u2071p8u")
        return cls(catalog_number,
                   lead_in_samples,
                   is_cdda,
                   [Flac_CUESHEET_track.parse(reader)
                    for i in range(track_count)])

    def build(self, writer):
        """writes this metadata block to a BitstreamWriter"""

        writer.build("128b64U1u2071p8u",
                     (self.__catalog_number__,
                      self.__lead_in_samples__,
                      self.__is_cdda__,
                      len(self.__tracks__)))
        for track in self.__tracks__:
            track.build(writer)

    def size(self):
        """the size of this metadata block
        not including the 4-byte block header"""

        return (396 +  # format_size("128b64U1u2071p8u") // 8
                sum(t.size() for t in self.__tracks__))

    def __len__(self):
        # don't include lead-out track
        return len(self.__tracks__) - 1

    def __getitem__(self, index):
        # don't include lead-out track
        return self.__tracks__[0:-1][index]

    def track_length(self, track_number, total_length=None):
        """given a track_number (typically starting from 1)
        and optional total length as a Fraction number of seconds
        (including the disc's pre-gap, if any),
        returns the length of the track as a Fraction number of seconds
        or None if the length is to the remainder of the stream
        (typically for the last track in the album)

        may raise KeyError if the track is not found"""

        initial_track = self.track(track_number)
        if (track_number + 1) in self.track_numbers():
            next_track = self.track(track_number + 1)
            return (next_track.index(1).offset() -
                    initial_track.index(1).offset())
        else:
            # getting track length of final track

            from fractions import Fraction

            lead_out_track = self.__tracks__[-1]
            final_index = initial_track.index(1)
            return (Fraction(lead_out_track.__offset__,
                             final_index.__sample_rate__) -
                    final_index.offset())

    def get_metadata(self):
        """returns MetaData of Sheet, or None
        this metadata often contains information such as catalog number
        or CD-TEXT values"""

        catalog = self.__catalog_number__.rstrip(b"\x00")
        if len(catalog) > 0:
            from audiotools import MetaData

            return MetaData(catalog=catalog.decode("ascii", "replace"))
        else:
            return None

    def set_track(self, audiofile):
        """sets the AudioFile this cuesheet belongs to

        this is necessary becuase FLAC's CUESHEET block
        doesn't store the file's sample rate
        which is needed to convert sample offsets to seconds"""

        for track in self:
            track.set_track(audiofile)

    @classmethod
    def converted(cls, sheet, total_pcm_frames, sample_rate, is_cdda=True):
        """given a Sheet object, total PCM frames, sample rate and
        optional boolean indicating whether cuesheet is CD audio
        returns a Flac_CUESHEET object from that data"""

        def pad(u, chars):
            if u is not None:
                s = u.encode("ascii", "replace")
                return s[0:chars] + (b"\x00" * (chars - len(s)))
            else:
                return b"\x00" * chars

        metadata = sheet.get_metadata()
        if (metadata is not None) and (metadata.catalog is not None):
            catalog_number = pad(metadata.catalog, 128)
        else:
            catalog_number = b"\x00" * 128

        # assume standard 2 second disc lead-in
        # and append empty lead-out track
        return cls(catalog_number=catalog_number,
                   lead_in_samples=sample_rate * 2,
                   is_cdda=(1 if is_cdda else 0),
                   tracks=([Flac_CUESHEET_track.converted(t, sample_rate)
                            for t in sheet] +
                           [Flac_CUESHEET_track(offset=total_pcm_frames,
                                                number=170 if is_cdda else 255,
                                                ISRC=b"\x00" * 12,
                                                track_type=0,
                                                pre_emphasis=0,
                                                index_points=[])]))


class Flac_CUESHEET_track(SheetTrack):
    def __init__(self, offset, number, ISRC, track_type, pre_emphasis,
                 index_points):
        """offset is the track's first index point's offset
        from the start of the stream, in PCM frames
        number is the track number, typically starting from 1
        ISRC is a 12 byte ASCII string, padded with NULLs
        track_type is 0 for audio, 1 for non-audio
        pre_emphasis is 0 for no, 1 for yes
        index_points is a list of Flac_CUESHEET_index objects"""

        assert(isinstance(offset, int) or isinstance(offset, long))
        assert(isinstance(number, int))
        assert(isinstance(ISRC, bytes))
        assert(track_type in {0, 1})
        assert(pre_emphasis in {0, 1})

        self.__offset__ = offset
        self.__number__ = number
        self.__ISRC__ = ISRC
        self.__track_type__ = track_type
        self.__pre_emphasis__ = pre_emphasis
        self.__index_points__ = index_points
        # the file this track belongs to
        self.__filename__ = ""

    @classmethod
    def converted(cls, sheet_track, sample_rate):
        """given a SheetTrack object and stream's sample rate,
        returns a Flac_CUESHEET_track object"""

        def pad(u, chars):
            if u is not None:
                s = u.encode("ascii", "replace")
                return s[0:chars] + (b"\x00" * (chars - len(s)))
            else:
                return b"\x00" * chars

        if len(sheet_track) > 0:
            offset = int(sheet_track[0].offset() * sample_rate)
        else:
            # track with no index points
            offset = 0

        metadata = sheet_track.get_metadata()

        if metadata is not None:
            ISRC = pad(metadata.ISRC, 12)
        else:
            ISRC = b"\x00" * 12

        return cls(offset=offset,
                   number=sheet_track.number(),
                   ISRC=ISRC,
                   track_type=(0 if sheet_track.is_audio() else 1),
                   pre_emphasis=(1 if sheet_track.pre_emphasis() else 0),
                   index_points=[Flac_CUESHEET_index.converted(
                       index, offset, sample_rate) for index in sheet_track])

    def copy(self):
        """returns a duplicate of this metadata block"""

        return Flac_CUESHEET_track(self.__offset__,
                                   self.__number__,
                                   self.__ISRC__,
                                   self.__track_type__,
                                   self.__pre_emphasis__,
                                   [index.copy() for index in
                                    self.__index_points__])

    def __repr__(self):
        return "Flac_CUESHEET_track({})".format(",".join(
            ["{}={!r}".format(key, getattr(self, "__" + key + "__"))
             for key in ["offset",
                         "number",
                         "ISRC",
                         "track_type",
                         "pre_emphasis",
                         "index_points"]]))

    def raw_info(self, indent):
        """returns a human-readable version of this track as unicode"""

        from os import linesep

        lines = [(u"track  : {number:3d}  " +
                  u"offset : {offset:9d}  " +
                  u"ISRC : {ISRC}").format(
                 number=self.__number__,
                 offset=self.__offset__,
                 type=self.__track_type__,
                 pre_emphasis=self.__pre_emphasis__,
                 ISRC=self.__ISRC__.strip(b"\x00").decode('ascii', 'replace'))
                 ] + [i.raw_info(1) for i in self.__index_points__]

        return linesep.join(
            [u" " * indent + line for line in lines])

    def __eq__(self, track):
        if isinstance(track, Flac_CUESHEET_track):
            return ((self.__offset__ == track.__offset__) and
                    (self.__number__ == track.__number__) and
                    (self.__ISRC__ == track.__ISRC__) and
                    (self.__track_type__ == track.__track_type__) and
                    (self.__pre_emphasis__ == track.__pre_emphasis__) and
                    (self.__index_points__ == track.__index_points__))
        else:
            return SheetTrack.__eq__(self, track)

    @classmethod
    def parse(cls, reader):
        """returns this cuesheet track from a BitstreamReader"""

        (offset,
         number,
         ISRC,
         track_type,
         pre_emphasis,
         index_points) = reader.parse("64U8u12b1u1u110p8u")
        return cls(offset, number, ISRC, track_type, pre_emphasis,
                   [Flac_CUESHEET_index.parse(reader, offset)
                    for i in range(index_points)])

    def build(self, writer):
        """writes this cuesheet track to a BitstreamWriter"""

        writer.build("64U8u12b1u1u110p8u",
                     (self.__offset__,
                      self.__number__,
                      self.__ISRC__,
                      self.__track_type__,
                      self.__pre_emphasis__,
                      len(self.__index_points__)))
        for index_point in self.__index_points__:
            index_point.build(writer)

    def size(self):
        return (36 +  # format_size("64U8u12b1u1u110p8u") // 8
                sum(i.size() for i in self.__index_points__))

    def __len__(self):
        return len(self.__index_points__)

    def __getitem__(self, index):
        return self.__index_points__[index]

    def number(self):
        """return SheetTrack's number, starting from 1"""

        return self.__number__

    def get_metadata(self):
        """returns SheetTrack's MetaData, or None"""

        isrc = self.__ISRC__.rstrip(b"\x00")
        if len(isrc) > 0:
            from audiotools import MetaData

            return MetaData(ISRC=isrc.decode("ascii", "replace"))
        else:
            return None

    def filename(self):
        """returns SheetTrack's filename as a unicode string"""

        from sys import version_info
        if version_info[0] >= 3:
            return self.__filename__
        else:
            return self.__filename__.decode("UTF-8")

    def is_audio(self):
        """returns whether SheetTrack contains audio data"""

        return True

    def pre_emphasis(self):
        """returns whether SheetTrack has pre-emphasis"""

        return self.__pre_emphasis__ == 1

    def copy_permitted(self):
        """returns whether copying is permitted"""

        return False

    def set_track(self, audiofile):
        """sets this track's source as the given AudioFile"""

        from os.path import basename

        self.__filename__ = basename(audiofile.filename)
        for index in self:
            index.set_track(audiofile)


class Flac_CUESHEET_index(SheetIndex):
    def __init__(self, track_offset, offset, number, sample_rate=44100):
        """track_offset is the index's track's offset in PCM frames

        offset is the index's offset from the track offset,
        in PCM frames
        number is the index's number typically starting from 1
        (a number of 0 indicates a track pre-gap)"""

        self.__track_offset__ = track_offset
        self.__offset__ = offset
        self.__number__ = number
        self.__sample_rate__ = sample_rate

    @classmethod
    def converted(cls, sheet_index, track_offset, sample_rate):
        """given a SheetIndex object, track_offset (in PCM frames)
        and sample rate, returns a Flac_CUESHEET_index object"""

        return cls(track_offset=track_offset,
                   offset=((int(sheet_index.offset() * sample_rate)) -
                           track_offset),
                   number=sheet_index.number(),
                   sample_rate=sample_rate)

    def copy(self):
        """returns a duplicate of this metadata block"""

        return Flac_CUESHEET_index(self.__track_offset__,
                                   self.__offset__,
                                   self.__number__,
                                   self.__sample_rate__)

    def __repr__(self):
        return "Flac_CUESHEET_index({!r}, {!r}, {!r}, {!r})".format(
            self.__track_offset__, self.__offset__,
            self.__number__, self.__sample_rate__)

    def __eq__(self, index):
        if isinstance(index, Flac_CUESHEET_index):
            return ((self.__offset__ == index.__offset__) and
                    (self.__number__ == index.__number__))
        else:
            return SheetIndex.__eq__(self, index)

    @classmethod
    def parse(cls, reader, track_offset):
        """returns this cuesheet index from a BitstreamReader"""

        (offset, number) = reader.parse("64U8u24p")

        return cls(track_offset=track_offset,
                   offset=offset,
                   number=number)

    def build(self, writer):
        """writes this cuesheet index to a BitstreamWriter"""

        writer.build("64U8u24p", (self.__offset__, self.__number__))

    def size(self):
        return 12  # format_size("64U8u24p") // 8

    def raw_info(self, indent):
        return ((u" " * indent) +
                u"index : {:3d}  offset : {:>9d}".format(
                    self.__number__,
                    self.__offset__))

    def number(self):
        return self.__number__

    def offset(self):
        from fractions import Fraction

        return Fraction(self.__track_offset__ + self.__offset__,
                        self.__sample_rate__)

    def set_track(self, audiofile):
        """sets this index's source to the given AudioFile"""

        self.__sample_rate__ = audiofile.sample_rate()


class Flac_PICTURE(Image):
    BLOCK_ID = 6

    def __init__(self, picture_type, mime_type, description,
                 width, height, color_depth, color_count, data):
        """
        picture_type - int of FLAC picture ID
        mime_type    - unicode string of MIME type
        description  - unicode string of description
        width        - int width value
        height       - int height value
        color_depth  - int bits-per-pixel value
        color_count  - int color count value
        data         - binary string of image data
        """

        from audiotools import PY3

        assert(isinstance(picture_type, int))
        assert(isinstance(mime_type, str if PY3 else unicode))
        assert(isinstance(description, str if PY3 else unicode))
        assert(isinstance(width, int))
        assert(isinstance(height, int))
        assert(isinstance(color_depth, int))
        assert(isinstance(color_count, int))
        assert(isinstance(data, bytes))

        # bypass Image's constructor and set block fields directly
        Image.__setattr__(self, "data", data)
        Image.__setattr__(self, "mime_type", mime_type)
        Image.__setattr__(self, "width", width)
        Image.__setattr__(self, "height", height)
        Image.__setattr__(self, "color_depth", color_depth)
        Image.__setattr__(self, "color_count", color_count)
        Image.__setattr__(self, "description", description)
        Image.__setattr__(self, "picture_type", picture_type)

    def copy(self):
        """returns a duplicate of this metadata block"""

        return Flac_PICTURE(self.picture_type,
                            self.mime_type,
                            self.description,
                            self.width,
                            self.height,
                            self.color_depth,
                            self.color_count,
                            self.data)

    def __getattr__(self, attr):
        if attr == "type":
            # convert FLAC picture_type to Image type
            #
            # | Item         | FLAC Picture ID | Image type |
            # |--------------+-----------------+------------|
            # | Other        |               0 |          4 |
            # | Front Cover  |               3 |          0 |
            # | Back Cover   |               4 |          1 |
            # | Leaflet Page |               5 |          2 |
            # | Media        |               6 |          3 |

            from audiotools import (FRONT_COVER,
                                    BACK_COVER,
                                    LEAFLET_PAGE,
                                    MEDIA,
                                    OTHER)

            return {0: OTHER,
                    3: FRONT_COVER,
                    4: BACK_COVER,
                    5: LEAFLET_PAGE,
                    6: MEDIA}.get(self.picture_type, OTHER)
        else:
            return Image.__getattribute__(self, attr)

    def __setattr__(self, attr, value):
        if attr == "type":
            # convert Image type to FLAC picture_type
            #
            # | Item         | Image type | FLAC Picture ID |
            # |--------------+------------+-----------------|
            # | Other        |          4 |               0 |
            # | Front Cover  |          0 |               3 |
            # | Back Cover   |          1 |               4 |
            # | Leaflet Page |          2 |               5 |
            # | Media        |          3 |               6 |

            from audiotools import (FRONT_COVER,
                                    BACK_COVER,
                                    LEAFLET_PAGE,
                                    MEDIA,
                                    OTHER)

            self.picture_type = {OTHER: 0,
                                 FRONT_COVER: 3,
                                 BACK_COVER: 4,
                                 LEAFLET_PAGE: 5,
                                 MEDIA: 6}.get(value, 0)
        else:
            Image.__setattr__(self, attr, value)

    def __repr__(self):
        return "Flac_PICTURE({})".format(",".join(
            ["{}={!r}".format(attr, getattr(self, attr))
             for attr in ["picture_type",
                          "mime_type",
                          "description",
                          "width",
                          "height",
                          "color_depth",
                          "color_count"]]))

    def raw_info(self):
        """returns a human-readable version of this metadata block
        as unicode"""

        from os import linesep

        return linesep.join(
            [u"  PICTURE:",
             u"    picture type = {:d}".format(self.picture_type),
             u"       MIME type = {}".format(self.mime_type),
             u"     description = {}".format(self.description),
             u"           width = {:d}".format(self.width),
             u"          height = {:d}".format(self.height),
             u"     color depth = {:d}".format(self.color_depth),
             u"     color count = {:d}".format(self.color_count),
             u"           bytes = {:d}".format(len(self.data))])

    @classmethod
    def parse(cls, reader):
        """returns this metadata block from a BitstreamReader"""

        picture_type = reader.read(32)
        mime_type = reader.read_bytes(reader.read(32)).decode('ascii')
        description = reader.read_bytes(reader.read(32)).decode('utf-8')
        width = reader.read(32)
        height = reader.read(32)
        color_depth = reader.read(32)
        color_count = reader.read(32)
        data = reader.read_bytes(reader.read(32))

        return cls(picture_type=picture_type,
                   mime_type=mime_type,
                   description=description,
                   width=width,
                   height=height,
                   color_depth=color_depth,
                   color_count=color_count,
                   data=data)

    def build(self, writer):
        """writes this metadata block to a BitstreamWriter"""

        writer.write(32, self.picture_type)
        mime_type = self.mime_type.encode('ascii')
        writer.write(32, len(mime_type))
        writer.write_bytes(mime_type)
        description = self.description.encode('utf-8')
        writer.write(32, len(description))
        writer.write_bytes(description)
        writer.write(32, self.width)
        writer.write(32, self.height)
        writer.write(32, self.color_depth)
        writer.write(32, self.color_count)
        writer.write(32, len(self.data))
        writer.write_bytes(self.data)

    def size(self):
        """the size of this metadata block
        not including the 4-byte block header"""

        return (4 +  # picture_type
                4 + len(self.mime_type.encode('ascii')) +
                4 + len(self.description.encode('utf-8')) +
                4 +  # width
                4 +  # height
                4 +  # color_count
                4 +  # color_depth
                4 + len(self.data))

    @classmethod
    def converted(cls, image):
        """converts an Image object to a FlacPictureComment"""

        return cls(
            picture_type={4: 0, 0: 3, 1: 4, 2: 5, 3: 6}.get(image.type, 0),
            mime_type=image.mime_type,
            description=image.description,
            width=image.width,
            height=image.height,
            color_depth=image.color_depth,
            color_count=image.color_count,
            data=image.data)

    def type_string(self):
        """returns the image's type as a human readable plain string

        for example, an image of type 0 returns "Front Cover"
        """

        return {0: u"Other",
                1: u"File icon",
                2: u"Other file icon",
                3: u"Cover (front)",
                4: u"Cover (back)",
                5: u"Leaflet page",
                6: u"Media",
                7: u"Lead artist / lead performer / soloist",
                8: u"Artist / Performer",
                9: u"Conductor",
                10: u"Band / Orchestra",
                11: u"Composer",
                12: u"Lyricist / Text writer",
                13: u"Recording Location",
                14: u"During recording",
                15: u"During performance",
                16: u"Movie / Video screen capture",
                17: u"A bright colored fish",
                18: u"Illustration",
                19: u"Band/Artist logotype",
                20: u"Publisher / Studio logotype"}.get(self.picture_type,
                                                        u"Other")

    def clean(self):
        from audiotools.image import image_metrics

        img = image_metrics(self.data)

        if (((self.mime_type != img.mime_type) or
             (self.width != img.width) or
             (self.height != img.height) or
             (self.color_depth != img.bits_per_pixel) or
             (self.color_count != img.color_count))):

            from audiotools.text import CLEAN_FIX_IMAGE_FIELDS

            return (self.__class__.converted(
                Image(type=self.type,
                      mime_type=img.mime_type,
                      description=self.description,
                      width=img.width,
                      height=img.height,
                      color_depth=img.bits_per_pixel,
                      color_count=img.color_count,
                      data=self.data)), [CLEAN_FIX_IMAGE_FIELDS])
        else:
            return (self, [])


class FlacAudio(WaveContainer, AiffContainer):
    """a Free Lossless Audio Codec file"""

    from audiotools.text import (COMP_FLAC_0,
                                 COMP_FLAC_8)

    SUFFIX = "flac"
    NAME = SUFFIX
    DESCRIPTION = u"Free Lossless Audio Codec"
    DEFAULT_COMPRESSION = "8"
    COMPRESSION_MODES = tuple(map(str, range(0, 9)))
    COMPRESSION_DESCRIPTIONS = {"0": COMP_FLAC_0,
                                "8": COMP_FLAC_8}

    METADATA_CLASS = FlacMetaData

    def __init__(self, filename):
        """filename is a plain string"""

        from audiotools.id3 import skip_id3v2_comment
        from audiotools.bitstream import BitstreamReader

        AudioFile.__init__(self, filename)

        # setup some dummy placeholder values
        self.__stream_offset__ = 0
        self.__samplerate__ = 0
        self.__channels__ = 0
        self.__bitspersample__ = 0
        self.__total_frames__ = 0
        self.__md5__ = b"\x00" * 16

        try:
            with open(self.filename, "rb") as f:
                # check for leading ID3v3 tag
                self.__stream_offset__ = skip_id3v2_comment(f)

                # ensure stream marker is correct
                if f.read(4) != b"fLaC":
                    from audiotools.text import ERR_FLAC_INVALID_FILE
                    raise InvalidFLAC(ERR_FLAC_INVALID_FILE)

                reader = BitstreamReader(f, False)

                # walk metadata blocks looking for STREAMINFO
                # (should be first block)
                stop = 0
                while stop == 0:
                    stop, header_type, length = reader.parse("1u 7u 24u")
                    if header_type == 0:
                        reader.skip(80)
                        self.__samplerate__ = reader.read(20)
                        self.__channels__ = reader.read(3) + 1
                        self.__bitspersample__ = reader.read(5) + 1
                        self.__total_frames__ = reader.read(36)
                        self.__md5__ = reader.read_bytes(16)
                        return
                    elif header_type in {1, 2, 3, 4, 5, 6}:
                        # be accepting of out-of-spec files
                        # whose STREAMINFO blocks aren't first
                        reader.skip_bytes(length)
                    else:
                        from audiotools.text import ERR_FLAC_INVALID_BLOCK
                        raise InvalidFLAC(ERR_FLAC_INVALID_BLOCK)
        except IOError as msg:
            raise InvalidFLAC(str(msg))

    def channel_mask(self):
        """returns a ChannelMask object of this track's channel layout"""

        from audiotools import ChannelMask

        if self.channels() <= 2:
            return ChannelMask.from_channels(self.channels())

        try:
            metadata = self.get_metadata()
            if metadata is not None:
                channel_mask = ChannelMask(
                    int(metadata.get_block(
                        Flac_VORBISCOMMENT.BLOCK_ID)[
                        u"WAVEFORMATEXTENSIBLE_CHANNEL_MASK"][0], 16))
                if len(channel_mask) == self.channels():
                    return channel_mask
                else:
                    # channel count mismatch in given mask
                    return ChannelMask(0)
            else:
                # proceed to generate channel mask
                raise ValueError()
        except (IndexError, KeyError, ValueError):
            # if there is no VORBIS_COMMENT block
            # or no WAVEFORMATEXTENSIBLE_CHANNEL_MASK in that block
            # or it's not an integer,
            # use FLAC's default mask based on channels
            if self.channels() == 3:
                return ChannelMask.from_fields(
                    front_left=True, front_right=True, front_center=True)
            elif self.channels() == 4:
                return ChannelMask.from_fields(
                    front_left=True, front_right=True,
                    back_left=True, back_right=True)
            elif self.channels() == 5:
                return ChannelMask.from_fields(
                    front_left=True, front_right=True, front_center=True,
                    back_left=True, back_right=True)
            elif self.channels() == 6:
                return ChannelMask.from_fields(
                    front_left=True, front_right=True, front_center=True,
                    back_left=True, back_right=True,
                    low_frequency=True)
            elif self.channels() == 7:
                return ChannelMask.from_fields(
                    front_left=True, front_right=True, front_center=True,
                    low_frequency=True, back_center=True,
                    side_left=True, side_right=True)
            elif self.channels() == 8:
                return ChannelMask.from_fields(
                    front_left=True, front_right=True, front_center=True,
                    low_frequency=True,
                    back_left=True, back_right=True,
                    side_left=True, side_right=True)
            else:
                # shouldn't be able to happen
                return ChannelMask(0)

    def lossless(self):
        """returns True"""

        return True

    @classmethod
    def supports_metadata(cls):
        """returns True if this audio type supports MetaData"""

        return True

    def get_metadata(self):
        """returns a MetaData object, or None

        raises IOError if unable to read the file"""

        from audiotools.bitstream import BitstreamReader

        # FlacAudio *always* returns a FlacMetaData object
        # even if the blocks aren't present
        # so there's no need to test for None

        with BitstreamReader(open(self.filename, 'rb'), False) as reader:
            reader.seek(self.__stream_offset__, 0)
            if reader.read_bytes(4) == b"fLaC":
                return FlacMetaData.parse(reader)
            else:
                # shouldn't be able to get here
                return None

    def update_metadata(self, metadata):
        """takes this track's current MetaData object
        as returned by get_metadata() and sets this track's metadata
        with any fields updated in that object

        raises IOError if unable to write the file
        """

        from audiotools.bitstream import BitstreamWriter
        from audiotools.bitstream import BitstreamReader
        from operator import add

        if metadata is None:
            return

        if not isinstance(metadata, FlacMetaData):
            from audiotools.text import ERR_FOREIGN_METADATA
            raise ValueError(ERR_FOREIGN_METADATA)

        old_metadata = self.get_metadata()
        padding_blocks = metadata.get_blocks(Flac_PADDING.BLOCK_ID)
        has_padding = len(padding_blocks) > 0
        padding_unchanged = (old_metadata.get_blocks(Flac_PADDING.BLOCK_ID) ==
                             padding_blocks)
        total_padding_size = sum(b.size() for b in padding_blocks)

        metadata_delta = metadata.size() - old_metadata.size()

        if (has_padding and padding_unchanged and
            (metadata_delta <= total_padding_size) and
            ((-metadata_delta + total_padding_size) <= MAX_PADDING_SIZE)):
            # if padding size is larger than change in metadata
            # shrink padding blocks so that new size matches old size
            # (if metadata_delta is negative,
            #  this will enlarge padding blocks as necessary)

            for padding in padding_blocks:
                if metadata_delta > 0:
                    # extract bytes from PADDING blocks
                    # until the metadata_delta is exhausted
                    if metadata_delta <= padding.length:
                        padding.length -= metadata_delta
                        metadata_delta = 0
                    else:
                        metadata_delta -= padding.length
                        padding.length = 0
                elif metadata_delta < 0:
                    # dump all our new bytes into the first PADDING block found
                    padding.length += -metadata_delta
                    metadata_delta = 0
                else:
                    break

            # then overwrite the beginning of the file
            stream = open(self.filename, 'r+b')
            stream.seek(self.__stream_offset__, 0)
            writer = BitstreamWriter(stream, 0)
            writer.write_bytes(b'fLaC')
            metadata.build(writer)
            writer.flush()
            writer.close()
        else:
            # if padding is smaller than change in metadata,
            # the padding would get excessively large,
            # or file has no padding blocks,
            # rewrite entire file to fit new metadata

            from audiotools import TemporaryFile, transfer_data
            from audiotools.bitstream import parse

            # dump any prefix data from old file to new one
            old_file = open(self.filename, "rb")
            new_file = TemporaryFile(self.filename)

            new_file.write(old_file.read(self.__stream_offset__))

            # skip existing file ID and metadata blocks
            if old_file.read(4) != b'fLaC':
                from audiotools.text import ERR_FLAC_INVALID_FILE
                raise InvalidFLAC(ERR_FLAC_INVALID_FILE)

            stop = 0
            while stop == 0:
                (stop, length) = parse("1u 7p 24u", False, old_file.read(4))
                old_file.read(length)

            # write new metadata to new file
            writer = BitstreamWriter(new_file, False)
            writer.write_bytes(b"fLaC")
            metadata.build(writer)

            # write remaining old data to new file
            transfer_data(old_file.read, writer.write_bytes)

            # commit change to disk
            old_file.close()
            writer.close()

    def set_metadata(self, metadata):
        """takes a MetaData object and sets this track's metadata

        this metadata includes track name, album name, and so on
        raises IOError if unable to read or write the file"""

        if metadata is None:
            return self.delete_metadata()

        new_metadata = self.METADATA_CLASS.converted(metadata)

        old_metadata = self.get_metadata()
        if old_metadata is None:
            # this shouldn't happen
            old_metadata = FlacMetaData([])

        # replace old metadata's VORBIS_COMMENT with one from new metadata
        # (if any)
        if new_metadata.has_block(Flac_VORBISCOMMENT.BLOCK_ID):
            new_vorbiscomment = new_metadata.get_block(
                Flac_VORBISCOMMENT.BLOCK_ID)

            if old_metadata.has_block(Flac_VORBISCOMMENT.BLOCK_ID):
                # both new and old metadata have a VORBIS_COMMENT block

                old_vorbiscomment = old_metadata.get_block(
                    Flac_VORBISCOMMENT.BLOCK_ID)

                # update vendor string from our current VORBIS_COMMENT block
                new_vorbiscomment.vendor_string = \
                    old_vorbiscomment.vendor_string

                # update REPLAYGAIN_* tags from
                # our current VORBIS_COMMENT block
                for key in [u"REPLAYGAIN_TRACK_GAIN",
                            u"REPLAYGAIN_TRACK_PEAK",
                            u"REPLAYGAIN_ALBUM_GAIN",
                            u"REPLAYGAIN_ALBUM_PEAK",
                            u"REPLAYGAIN_REFERENCE_LOUDNESS"]:
                    try:
                        new_vorbiscomment[key] = old_vorbiscomment[key]
                    except KeyError:
                        new_vorbiscomment[key] = []

                # update WAVEFORMATEXTENSIBLE_CHANNEL_MASK
                # from our current VORBIS_COMMENT block, if any
                if (((self.channels() > 2) or
                     (self.bits_per_sample() > 16)) and
                    (u"WAVEFORMATEXTENSIBLE_CHANNEL_MASK" in
                     old_vorbiscomment.keys())):
                    new_vorbiscomment[u"WAVEFORMATEXTENSIBLE_CHANNEL_MASK"] = \
                        old_vorbiscomment[u"WAVEFORMATEXTENSIBLE_CHANNEL_MASK"]
                elif (u"WAVEFORMATEXTENSIBLE_CHANNEL_MASK" in
                      new_vorbiscomment.keys()):
                    new_vorbiscomment[
                        u"WAVEFORMATEXTENSIBLE_CHANNEL_MASK"] = []

                # update CDTOC from our current VORBIS_COMMENT block, if any
                try:
                    new_vorbiscomment[u"CDTOC"] = old_vorbiscomment[u"CDTOC"]
                except KeyError:
                    new_vorbiscomment[u"CDTOC"] = []

                old_metadata.replace_blocks(Flac_VORBISCOMMENT.BLOCK_ID,
                                            [new_vorbiscomment])
            else:
                # new metadata has VORBIS_COMMENT block,
                # but old metadata does not

                # remove REPLAYGAIN_* tags from new VORBIS_COMMENT block
                for key in [u"REPLAYGAIN_TRACK_GAIN",
                            u"REPLAYGAIN_TRACK_PEAK",
                            u"REPLAYGAIN_ALBUM_GAIN",
                            u"REPLAYGAIN_ALBUM_PEAK",
                            u"REPLAYGAIN_REFERENCE_LOUDNESS"]:
                    new_vorbiscomment[key] = []

                # update WAVEFORMATEXTENSIBLE_CHANNEL_MASK
                # from our actual mask if necessary
                if (self.channels() > 2) or (self.bits_per_sample() > 16):
                    new_vorbiscomment[u"WAVEFORMATEXTENSIBLE_CHANNEL_MASK"] = [
                        u"0x{:04X}".format(self.channel_mask())]

                # remove CDTOC from new VORBIS_COMMENT block
                new_vorbiscomment[u"CDTOC"] = []

                old_metadata.add_block(new_vorbiscomment)
        else:
            # new metadata has no VORBIS_COMMENT block
            pass

        # replace old metadata's PICTURE blocks with those from new metadata
        old_metadata.replace_blocks(
            Flac_PICTURE.BLOCK_ID,
            new_metadata.get_blocks(Flac_PICTURE.BLOCK_ID))

        # everything else remains as-is

        self.update_metadata(old_metadata)

    def delete_metadata(self):
        """deletes the track's MetaData

        this removes or unsets tags as necessary in order to remove all data
        raises IOError if unable to write the file"""

        self.set_metadata(MetaData())

    @classmethod
    def supports_cuesheet(cls):
        return True

    def set_cuesheet(self, cuesheet):
        """imports cuesheet data from a Sheet object

        Raises IOError if an error occurs setting the cuesheet"""

        if cuesheet is not None:
            # overwrite old cuesheet (if any) with new block
            metadata = self.get_metadata()
            metadata.replace_blocks(
                Flac_CUESHEET.BLOCK_ID,
                [Flac_CUESHEET.converted(
                    cuesheet,
                    self.total_frames(),
                    self.sample_rate(),
                    (self.sample_rate() == 44100) and
                    (self.channels() == 2) and
                    (self.bits_per_sample() == 16) and
                    (len(cuesheet) <= 99))])

            # wipe out any CDTOC tag
            try:
                vorbiscomment = metadata.get_block(Flac_VORBISCOMMENT.BLOCK_ID)
                if u"CDTOC" in vorbiscomment:
                    del(vorbiscomment[u"CDTOC"])
            except IndexError:
                pass

            self.update_metadata(metadata)
        else:
            self.delete_cuesheet()

    def get_cuesheet(self):
        """returns the embedded Sheet object, or None

        Raises IOError if a problem occurs when reading the file"""

        metadata = self.get_metadata()

        # first, check for a CUESHEET block
        try:
            cuesheet = metadata.get_block(Flac_CUESHEET.BLOCK_ID)
            cuesheet.set_track(self)
            return cuesheet
        except IndexError:
            pass

        # then, check for a CUESHEET tag or CDTOC tag
        try:
            vorbiscomment = metadata.get_block(Flac_VORBISCOMMENT.BLOCK_ID)
            if u"CUESHEET" in vorbiscomment:
                from audiotools import SheetException
                from audiotools.cue import read_cuesheet_string
                try:
                    return read_cuesheet_string(vorbiscomment[u"CUESHEET"][0])
                except SheetException:
                    pass
            if u"CDTOC" in vorbiscomment:
                from audiotools import SheetException
                from audiotools.toc import read_tocfile_string
                try:
                    return read_tocfile_string(vorbiscomment[u"CDTOC"][0])
                except SheetException:
                    from audiotools.cdtoc import CDTOC
                    try:
                        return CDTOC.from_unicode(vorbiscomment[u"CDTOC"][0])
                    except ValueError:
                        pass
        except IndexError:
            pass

        return None

    def delete_cuesheet(self):
        """deletes embedded Sheet object, if any

        Raises IOError if a problem occurs when updating the file"""

        metadata = self.get_metadata()

        # wipe out any CUESHEET blocks
        metadata.replace_blocks(Flac_CUESHEET.BLOCK_ID, [])

        # then erase any CDTOC tags
        try:
            vorbiscomment = metadata.get_block(Flac_VORBISCOMMENT.BLOCK_ID)
            del(vorbiscomment[u"CDTOC"])
        except IndexError:
            pass
        self.update_metadata(metadata)

    def to_pcm(self):
        """returns a PCMReader object containing the track's PCM data"""

        from audiotools.decoders import FlacDecoder
        from audiotools import PCMReaderError

        try:
            flac = open(self.filename, "rb")
        except (IOError, ValueError) as err:
            return PCMReaderError(error_message=str(err),
                                  sample_rate=self.sample_rate(),
                                  channels=self.channels(),
                                  channel_mask=int(self.channel_mask()),
                                  bits_per_sample=self.bits_per_sample())

        try:
            if self.__stream_offset__ > 0:
                flac.seek(self.__stream_offset__)
            return FlacDecoder(flac)
        except (IOError, ValueError) as err:
            # The only time this is likely to occur is
            # if the FLAC is modified between when FlacAudio
            # is initialized and when to_pcm() is called.
            flac.close()
            return PCMReaderError(error_message=str(err),
                                  sample_rate=self.sample_rate(),
                                  channels=self.channels(),
                                  channel_mask=int(self.channel_mask()),
                                  bits_per_sample=self.bits_per_sample())

    @classmethod
    def supports_to_pcm(cls):
        try:
            from audiotools.decoders import FlacDecoder
            return True
        except ImportError:
            return False

    @classmethod
    def from_pcm(cls, filename, pcmreader,
                 compression=None,
                 total_pcm_frames=None,
                 encoding_function=None):
        """encodes a new file from PCM data

        takes a filename string, PCMReader object,
        optional compression level string and
        optional total_pcm_frames integer
        encodes a new audio file from pcmreader's data
        at the given filename with the specified compression level
        and returns a new FlacAudio object"""

        from audiotools.encoders import encode_flac
        from audiotools import EncodingError
        from audiotools import __default_quality__
        from audiotools import VERSION

        if ((compression is None) or (compression not in
                                      cls.COMPRESSION_MODES)):
            compression = __default_quality__(cls.NAME)

        encoding_options = {
            "0": {"block_size": 1152,
                  "max_lpc_order": 0,
                  "min_residual_partition_order": 0,
                  "max_residual_partition_order": 3},
            "1": {"block_size": 1152,
                  "max_lpc_order": 0,
                  "adaptive_mid_side": True,
                  "min_residual_partition_order": 0,
                  "max_residual_partition_order": 3},
            "2": {"block_size": 1152,
                  "max_lpc_order": 0,
                  "exhaustive_model_search": True,
                  "min_residual_partition_order": 0,
                  "max_residual_partition_order": 3},
            "3": {"block_size": 4096,
                  "max_lpc_order": 6,
                  "min_residual_partition_order": 0,
                  "max_residual_partition_order": 4},
            "4": {"block_size": 4096,
                  "max_lpc_order": 8,
                  "adaptive_mid_side": True,
                  "min_residual_partition_order": 0,
                  "max_residual_partition_order": 4},
            "5": {"block_size": 4096,
                  "max_lpc_order": 8,
                  "mid_side": True,
                  "min_residual_partition_order": 0,
                  "max_residual_partition_order": 5},
            "6": {"block_size": 4096,
                  "max_lpc_order": 8,
                  "mid_side": True,
                  "min_residual_partition_order": 0,
                  "max_residual_partition_order": 6},
            "7": {"block_size": 4096,
                  "max_lpc_order": 8,
                  "mid_side": True,
                  "exhaustive_model_search": True,
                  "min_residual_partition_order": 0,
                  "max_residual_partition_order": 6},
            "8": {"block_size": 4096,
                  "max_lpc_order": 12,
                  "mid_side": True,
                  "exhaustive_model_search": True,
                  "min_residual_partition_order": 0,
                  "max_residual_partition_order": 6}}[compression]

        if pcmreader.bits_per_sample not in {8, 16, 24}:
            from audiotools import UnsupportedBitsPerSample
            pcmreader.close()
            raise UnsupportedBitsPerSample(filename, pcmreader.bits_per_sample)

        if pcmreader.channels > 8:
            from audiotools import UnsupportedChannelCount
            pcmreader.close()
            raise UnsupportedChannelCount(filename, pcmreader.channels)

        if (pcmreader.channel_mask not in
                {0x0001,  # 1ch - mono
                 0x0004,  # 1ch - mono
                 0x0003,  # 2ch - left, right
                 0x0007,  # 3ch - left, right, center
                 0x0033,  # 4ch - left, right, back left, back right
                 0x0603,  # 4ch - left, right, side left, side right
                 0x0037,  # 5ch - L, R, C, back left, back right
                 0x0607,  # 5ch - L, R, C, side left, side right
                 0x003F,  # 6ch - L, R, C, LFE, back left, back right
                 0x060F,  # 6ch - L, R, C, LFE, side left, side right
                 0}):
            from audiotools import UnsupportedChannelMask
            pcmreader.close()
            raise UnsupportedChannelMask(filename, pcmreader.channel_mask)

        try:
            (encode_flac if encoding_function is None else encoding_function)(
                filename=filename,
                pcmreader=pcmreader,
                version="Python Audio Tools " + VERSION,
                total_pcm_frames=(total_pcm_frames if
                                  total_pcm_frames is not None else 0),
                padding_size=4096,
                **encoding_options)

            return FlacAudio(filename)
        except (IOError, ValueError) as err:
            cls.__unlink__(filename)
            raise EncodingError(str(err))
        except Exception:
            cls.__unlink__(filename)
            raise
        finally:
            pcmreader.close()

    @classmethod
    def supports_from_pcm(cls):
        try:
            from audiotools.encoders import encode_flac
            return True
        except ImportError:
            return False

    def seekable(self):
        """returns True if the file is seekable"""

        return self.get_metadata().has_block(Flac_SEEKTABLE.BLOCK_ID)

    def seektable(self, offsets=None, seekpoint_interval=None):
        """returns a new Flac_SEEKTABLE object
        created from parsing the FLAC file itself"""

        from bisect import bisect_right

        if offsets is None:
            sizes = []
            with self.to_pcm() as pcmreader:
                pair = pcmreader.frame_size()
                while pair is not None:
                    sizes.append(pair)
                    pair = pcmreader.frame_size()
            offsets = sizes_to_offsets(sizes)

        if seekpoint_interval is None:
            seekpoint_interval = self.sample_rate() * 10

        total_samples = 0
        all_frames = {}
        sample_offsets = []
        for (byte_offset, pcm_frames) in offsets:
            all_frames[total_samples] = (byte_offset, pcm_frames)
            sample_offsets.append(total_samples)
            total_samples += pcm_frames

        seekpoints = []
        for pcm_frame in range(0, self.total_frames(), seekpoint_interval):
            flac_frame = bisect_right(sample_offsets, pcm_frame) - 1
            seekpoints.append((sample_offsets[flac_frame],
                               all_frames[sample_offsets[flac_frame]][0],
                               all_frames[sample_offsets[flac_frame]][1]))

        return Flac_SEEKTABLE(seekpoints)

    def has_foreign_wave_chunks(self):
        """returns True if the audio file contains non-audio RIFF chunks

        during transcoding, if the source audio file has foreign RIFF chunks
        and the target audio format supports foreign RIFF chunks,
        conversion should be routed through .wav conversion
        to avoid losing those chunks"""

        try:
            return b'riff' in [
                block.application_id for block in
                self.get_metadata().get_blocks(Flac_APPLICATION.BLOCK_ID)]
        except IOError:
            return False

    def wave_header_footer(self):
        """returns (header, footer) tuple of strings
        containing all data before and after the PCM stream

        may raise ValueError if there's a problem with
        the header or footer data
        may raise IOError if there's a problem reading
        header or footer data from the file
        """

        from audiotools.wav import pad_data

        header = []
        if (pad_data(self.total_frames(),
                     self.channels(),
                     self.bits_per_sample())):
            footer = [b"\x00"]
        else:
            footer = []
        current_block = header

        metadata = self.get_metadata()

        # convert individual chunks into combined header and footer strings
        for block in metadata.get_blocks(Flac_APPLICATION.BLOCK_ID):
            if block.application_id == b"riff":
                chunk_id = block.data[0:4]
                # combine APPLICATION metadata blocks up to "data" as header
                if chunk_id != b"data":
                    current_block.append(block.data)
                else:
                    # combine APPLICATION metadata blocks past "data" as footer
                    current_block.append(block.data)
                    current_block = footer

        # return tuple of header and footer
        if (len(header) != 0) or (len(footer) != 0):
            return (b"".join(header), b"".join(footer))
        else:
            raise ValueError("no foreign RIFF chunks")

    @classmethod
    def from_wave(cls, filename, header, pcmreader, footer, compression=None):
        """encodes a new file from wave data

        takes a filename string, header string,
        PCMReader object, footer string
        and optional compression level string
        encodes a new audio file from pcmreader's data
        at the given filename with the specified compression level
        and returns a new WaveAudio object

        may raise EncodingError if some problem occurs when
        encoding the input file"""

        from io import BytesIO
        from audiotools.bitstream import BitstreamReader
        from audiotools.bitstream import BitstreamRecorder
        from audiotools.bitstream import format_byte_size
        from audiotools.wav import (pad_data, WaveAudio)
        from audiotools import (EncodingError, CounterPCMReader)

        # split header and footer into distinct chunks
        header_len = len(header)
        footer_len = len(footer)
        fmt_found = False
        blocks = []
        try:
            # read everything from start of header to "data<size>"
            # chunk header
            r = BitstreamReader(BytesIO(header), True)
            (riff, remaining_size, wave) = r.parse("4b 32u 4b")
            if riff != b"RIFF":
                from audiotools.text import ERR_WAV_NOT_WAVE
                raise EncodingError(ERR_WAV_NOT_WAVE)
            elif wave != b"WAVE":
                from audiotools.text import ERR_WAV_INVALID_WAVE
                raise EncodingError(ERR_WAV_INVALID_WAVE)
            else:
                block_data = BitstreamRecorder(True)
                block_data.build("4b 32u 4b", (riff, remaining_size, wave))
                blocks.append(Flac_APPLICATION(b"riff", block_data.data()))
                total_size = remaining_size + 8
                header_len -= format_byte_size("4b 32u 4b")

            while header_len:
                block_data = BitstreamRecorder(True)
                (chunk_id, chunk_size) = r.parse("4b 32u")
                # ensure chunk ID is valid
                if (not frozenset(chunk_id).issubset(
                        WaveAudio.PRINTABLE_ASCII)):
                    from audiotools.text import ERR_WAV_INVALID_CHUNK
                    raise EncodingError(ERR_WAV_INVALID_CHUNK)
                else:
                    header_len -= format_byte_size("4b 32u")
                    block_data.build("4b 32u", (chunk_id, chunk_size))

                if chunk_id == b"data":
                    # transfer only "data" chunk header to APPLICATION block
                    if header_len != 0:
                        from audiotools.text import ERR_WAV_HEADER_EXTRA_DATA
                        raise EncodingError(
                            ERR_WAV_HEADER_EXTRA_DATA.format(header_len))
                    elif not fmt_found:
                        from audiotools.text import ERR_WAV_NO_FMT_CHUNK
                        raise EncodingError(ERR_WAV_NO_FMT_CHUNK)
                    else:
                        blocks.append(
                            Flac_APPLICATION(b"riff", block_data.data()))
                        data_chunk_size = chunk_size
                        break
                elif chunk_id == b"fmt ":
                    if not fmt_found:
                        fmt_found = True
                        if chunk_size % 2:
                            # transfer padded chunk to APPLICATION block
                            block_data.write_bytes(
                                r.read_bytes(chunk_size + 1))
                            header_len -= (chunk_size + 1)
                        else:
                            # transfer un-padded chunk to APPLICATION block
                            block_data.write_bytes(
                                r.read_bytes(chunk_size))
                            header_len -= chunk_size

                        blocks.append(
                            Flac_APPLICATION(b"riff", block_data.data()))
                    else:
                        from audiotools.text import ERR_WAV_MULTIPLE_FMT
                        raise EncodingError(ERR_WAV_MULTIPLE_FMT)
                else:
                    if chunk_size % 2:
                        # transfer padded chunk to APPLICATION block
                        block_data.write_bytes(r.read_bytes(chunk_size + 1))
                        header_len -= (chunk_size + 1)
                    else:
                        # transfer un-padded chunk to APPLICATION block
                        block_data.write_bytes(r.read_bytes(chunk_size))
                        header_len -= chunk_size

                    blocks.append(Flac_APPLICATION(b"riff", block_data.data()))
            else:
                from audiotools.text import ERR_WAV_NO_DATA_CHUNK
                raise EncodingError(ERR_WAV_NO_DATA_CHUNK)
        except IOError:
            from audiotools.text import ERR_WAV_HEADER_IOERROR
            raise EncodingError(ERR_WAV_HEADER_IOERROR)

        try:
            # read everything from start of footer to end of footer
            r = BitstreamReader(BytesIO(footer), True)
            # skip initial footer pad byte
            if data_chunk_size % 2:
                r.skip_bytes(1)
                footer_len -= 1

            while footer_len:
                block_data = BitstreamRecorder(True)
                (chunk_id, chunk_size) = r.parse("4b 32u")

                if (not frozenset(chunk_id).issubset(
                        WaveAudio.PRINTABLE_ASCII)):
                    # ensure chunk ID is valid
                    from audiotools.text import ERR_WAV_INVALID_CHUNK
                    raise EncodingError(ERR_WAV_INVALID_CHUNK)
                elif chunk_id == b"fmt ":
                    # multiple "fmt " chunks is an error
                    from audiotools.text import ERR_WAV_MULTIPLE_FMT
                    raise EncodingError(ERR_WAV_MULTIPLE_FMT)
                elif chunk_id == b"data":
                    # multiple "data" chunks is an error
                    from audiotools.text import ERR_WAV_MULTIPLE_DATA
                    raise EncodingError(ERR_WAV_MULTIPLE_DATA)
                else:
                    footer_len -= format_byte_size("4b 32u")
                    block_data.build("4b 32u", (chunk_id, chunk_size))

                    if chunk_size % 2:
                        # transfer padded chunk to APPLICATION block
                        block_data.write_bytes(r.read_bytes(chunk_size + 1))
                        footer_len -= (chunk_size + 1)
                    else:
                        # transfer un-padded chunk to APPLICATION block
                        block_data.write_bytes(r.read_bytes(chunk_size))
                        footer_len -= chunk_size

                    blocks.append(Flac_APPLICATION(b"riff", block_data.data()))
        except IOError:
            from audiotools.text import ERR_WAV_FOOTER_IOERROR
            raise EncodingError(ERR_WAV_FOOTER_IOERROR)

        counter = CounterPCMReader(pcmreader)

        # perform standard FLAC encode from PCMReader
        flac = cls.from_pcm(filename, counter, compression)

        data_bytes_written = counter.bytes_written()

        # ensure processed PCM data equals size of "data" chunk
        if data_bytes_written != data_chunk_size:
            cls.__unlink__(filename)
            from audiotools.text import ERR_WAV_TRUNCATED_DATA_CHUNK
            raise EncodingError(ERR_WAV_TRUNCATED_DATA_CHUNK)

        # ensure total size of header + PCM + footer matches wav's header
        if (len(header) + data_bytes_written + len(footer)) != total_size:
            cls.__unlink__(filename)
            from audiotools.text import ERR_WAV_INVALID_SIZE
            raise EncodingError(ERR_WAV_INVALID_SIZE)

        # add chunks as APPLICATION metadata blocks
        metadata = flac.get_metadata()
        for block in blocks:
            metadata.add_block(block)
        flac.update_metadata(metadata)

        # return encoded FLAC file
        return flac

    def has_foreign_aiff_chunks(self):
        """returns True if the audio file contains non-audio AIFF chunks"""

        try:
            return b'aiff' in [
                block.application_id for block in
                self.get_metadata().get_blocks(Flac_APPLICATION.BLOCK_ID)]
        except IOError:
            return False

    def aiff_header_footer(self):
        """returns (header, footer) tuple of strings
        containing all data before and after the PCM stream

        if self.has_foreign_aiff_chunks() is False,
        may raise ValueError if the file has no header and footer
        for any reason"""

        from audiotools.aiff import pad_data

        header = []
        if (pad_data(self.total_frames(),
                     self.channels(),
                     self.bits_per_sample())):
            footer = [b"\x00"]
        else:
            footer = []
        current_block = header

        metadata = self.get_metadata()
        if metadata is None:
            raise ValueError("no foreign AIFF chunks")

        # convert individual chunks into combined header and footer strings
        for block in metadata.get_blocks(Flac_APPLICATION.BLOCK_ID):
            if block.application_id == b"aiff":
                chunk_id = block.data[0:4]
                # combine APPLICATION metadata blocks up to "SSND" as header
                if chunk_id != b"SSND":
                    current_block.append(block.data)
                else:
                    # combine APPLICATION metadata blocks past "SSND" as footer
                    current_block.append(block.data)
                    current_block = footer

        # return tuple of header and footer
        if (len(header) != 0) or (len(footer) != 0):
            return (b"".join(header), b"".join(footer))
        else:
            raise ValueError("no foreign AIFF chunks")

    @classmethod
    def from_aiff(cls, filename, header, pcmreader, footer, compression=None):
        """encodes a new file from AIFF data

        takes a filename string, header string,
        PCMReader object, footer string
        and optional compression level string
        encodes a new audio file from pcmreader's data
        at the given filename with the specified compression level
        and returns a new AiffAudio object

        header + pcm data + footer should always result
        in the original AIFF file being restored
        without need for any padding bytes

        may raise EncodingError if some problem occurs when
        encoding the input file"""

        from io import BytesIO
        from audiotools.bitstream import BitstreamReader
        from audiotools.bitstream import BitstreamRecorder
        from audiotools.bitstream import format_byte_size
        from audiotools.aiff import (pad_data, AiffAudio)
        from audiotools import (EncodingError, CounterPCMReader)

        # split header and footer into distinct chunks
        header_len = len(header)
        footer_len = len(footer)
        comm_found = False
        blocks = []
        try:
            # read everything from start of header to "SSND<size>"
            # chunk header
            r = BitstreamReader(BytesIO(header), False)
            (form, remaining_size, aiff) = r.parse("4b 32u 4b")
            if form != b"FORM":
                from audiotools.text import ERR_AIFF_NOT_AIFF
                raise EncodingError(ERR_AIFF_NOT_AIFF)
            elif aiff != b"AIFF":
                from audiotools.text import ERR_AIFF_INVALID_AIFF
                raise EncodingError(ERR_AIFF_INVALID_AIFF)
            else:
                block_data = BitstreamRecorder(0)
                block_data.build("4b 32u 4b", (form, remaining_size, aiff))
                blocks.append(Flac_APPLICATION("aiff", block_data.data()))
                total_size = remaining_size + 8
                header_len -= format_byte_size("4b 32u 4b")

            while header_len:
                block_data = BitstreamRecorder(0)
                (chunk_id, chunk_size) = r.parse("4b 32u")
                # ensure chunk ID is valid
                if (not frozenset(chunk_id).issubset(
                        AiffAudio.PRINTABLE_ASCII)):
                    from audiotools.text import ERR_AIFF_INVALID_CHUNK
                    raise EncodingError(ERR_AIFF_INVALID_CHUNK)
                else:
                    header_len -= format_byte_size("4b 32u")
                    block_data.build("4b 32u", (chunk_id, chunk_size))

                if chunk_id == b"SSND":
                    from audiotools.text import (ERR_AIFF_HEADER_EXTRA_SSND,
                                                 ERR_AIFF_HEADER_MISSING_SSND,
                                                 ERR_AIFF_NO_COMM_CHUNK)

                    # transfer only "SSND" chunk header to APPLICATION block
                    # (including 8 bytes after ID/size header)
                    if header_len > 8:
                        raise EncodingError(ERR_AIFF_HEADER_EXTRA_SSND)
                    elif header_len < 8:
                        raise EncodingError(ERR_AIFF_HEADER_MISSING_SSND)
                    elif not comm_found:
                        raise EncodingError(ERR_AIFF_NO_COMM_CHUNK)
                    else:
                        block_data.write_bytes(r.read_bytes(8))
                        blocks.append(
                            Flac_APPLICATION(b"aiff", block_data.data()))
                        ssnd_chunk_size = (chunk_size - 8)
                        break
                elif chunk_id == b"COMM":
                    from audiotools.text import ERR_AIFF_MULTIPLE_COMM_CHUNKS

                    if not comm_found:
                        comm_found = True
                        if chunk_size % 2:
                            # transfer padded chunk to APPLICATION block
                            block_data.write_bytes(
                                r.read_bytes(chunk_size + 1))
                            header_len -= (chunk_size + 1)
                        else:
                            # transfer un-padded chunk to APPLICATION block
                            block_data.write_bytes(
                                r.read_bytes(chunk_size))
                            header_len -= chunk_size
                        blocks.append(
                            Flac_APPLICATION(b"aiff", block_data.data()))
                    else:
                        raise EncodingError(ERR_AIFF_MULTIPLE_COMM_CHUNKS)
                else:
                    if chunk_size % 2:
                        # transfer padded chunk to APPLICATION block
                        block_data.write_bytes(r.read_bytes(chunk_size + 1))
                        header_len -= (chunk_size + 1)
                    else:
                        # transfer un-padded chunk to APPLICATION block
                        block_data.write_bytes(r.read_bytes(chunk_size))
                        header_len -= chunk_size

                    blocks.append(Flac_APPLICATION(b"aiff", block_data.data()))
            else:
                from audiotools.text import ERR_AIFF_NO_SSND_CHUNK
                raise EncodingError(ERR_AIFF_NO_SSND_CHUNK)
        except IOError:
            from audiotools.text import ERR_AIFF_HEADER_IOERROR
            raise EncodingError(ERR_AIFF_HEADER_IOERROR)

        try:
            # read everything from start of footer to end of footer
            r = BitstreamReader(BytesIO(footer), False)
            # skip initial footer pad byte
            if ssnd_chunk_size % 2:
                r.skip_bytes(1)
                footer_len -= 1

            while footer_len:
                block_data = BitstreamRecorder(0)
                (chunk_id, chunk_size) = r.parse("4b 32u")

                if (not frozenset(chunk_id).issubset(
                        AiffAudio.PRINTABLE_ASCII)):
                    # ensure chunk ID is valid
                    from audiotools.text import ERR_AIFF_INVALID_CHUNK
                    raise EncodingError(ERR_AIFF_INVALID_CHUNK)
                elif chunk_id == b"COMM":
                    # multiple "COMM" chunks is an error
                    from audiotools.text import ERR_AIFF_MULTIPLE_COMM_CHUNKS
                    raise EncodingError(ERR_AIFF_MULTIPLE_COMM_CHUNKS)
                elif chunk_id == b"SSND":
                    # multiple "SSND" chunks is an error
                    from audiotools.text import ERR_AIFF_MULTIPLE_SSND_CHUNKS
                    raise EncodingError(ERR_AIFF_MULTIPLE_SSND_CHUNKS)
                else:
                    footer_len -= format_byte_size("4b 32u")
                    block_data.build("4b 32u", (chunk_id, chunk_size))

                    if chunk_size % 2:
                        # transfer padded chunk to APPLICATION block
                        block_data.write_bytes(r.read_bytes(chunk_size + 1))
                        footer_len -= (chunk_size + 1)
                    else:
                        # transfer un-padded chunk to APPLICATION block
                        block_data.write_bytes(r.read_bytes(chunk_size))
                        footer_len -= chunk_size

                    blocks.append(Flac_APPLICATION(b"aiff", block_data.data()))
        except IOError:
            from audiotools.text import ERR_AIFF_FOOTER_IOERROR
            raise EncodingError(ERR_AIFF_FOOTER_IOERROR)

        counter = CounterPCMReader(pcmreader)

        # perform standard FLAC encode from PCMReader
        flac = cls.from_pcm(filename, counter, compression)

        ssnd_bytes_written = counter.bytes_written()

        # ensure processed PCM data equals size of "SSND" chunk
        if ssnd_bytes_written != ssnd_chunk_size:
            cls.__unlink__(filename)
            from audiotools.text import ERR_AIFF_TRUNCATED_SSND_CHUNK
            raise EncodingError(ERR_AIFF_TRUNCATED_SSND_CHUNK)

        # ensure total size of header + PCM + footer matches aiff's header
        if (len(header) + ssnd_bytes_written + len(footer)) != total_size:
            cls.__unlink__(filename)
            from audiotools.text import ERR_AIFF_INVALID_SIZE
            raise EncodingError(ERR_AIFF_INVALID_SIZE)

        # add chunks as APPLICATION metadata blocks
        metadata = flac.get_metadata()
        if metadata is not None:
            for block in blocks:
                metadata.add_block(block)
            flac.update_metadata(metadata)

        # return encoded FLAC file
        return flac

    def convert(self, target_path, target_class, compression=None,
                progress=None):
        """encodes a new AudioFile from existing AudioFile

        take a filename string, target class and optional compression string
        encodes a new AudioFile in the target class and returns
        the resulting object
        may raise EncodingError if some problem occurs during encoding"""

        # If a FLAC has embedded RIFF *and* embedded AIFF chunks,
        # RIFF takes precedence if the target format supports both.
        # (it's hard to envision a scenario in which that would happen)

        from audiotools import WaveAudio
        from audiotools import AiffAudio
        from audiotools import to_pcm_progress

        if ((self.has_foreign_wave_chunks() and
             hasattr(target_class, "from_wave") and
             callable(target_class.from_wave))):
            return WaveContainer.convert(self,
                                         target_path,
                                         target_class,
                                         compression,
                                         progress)
        elif (self.has_foreign_aiff_chunks() and
              hasattr(target_class, "from_aiff") and
              callable(target_class.from_aiff)):
            return AiffContainer.convert(self,
                                         target_path,
                                         target_class,
                                         compression,
                                         progress)
        else:
            return target_class.from_pcm(
                target_path,
                to_pcm_progress(self, progress),
                compression,
                total_pcm_frames=self.total_frames())

    def bits_per_sample(self):
        """returns an integer number of bits-per-sample this track contains"""

        return self.__bitspersample__

    def channels(self):
        """returns an integer number of channels this track contains"""

        return self.__channels__

    def total_frames(self):
        """returns the total PCM frames of the track as an integer"""

        return self.__total_frames__

    def sample_rate(self):
        """returns the rate of the track's audio as an integer number of Hz"""

        return self.__samplerate__

    @classmethod
    def supports_replay_gain(cls):
        """returns True if this class supports ReplayGain"""

        return True

    def get_replay_gain(self):
        """returns a ReplayGain object of our ReplayGain values

        returns None if we have no values"""

        from audiotools import ReplayGain

        try:
            vorbis_metadata = self.get_metadata().get_block(
                Flac_VORBISCOMMENT.BLOCK_ID)
        except (IndexError, IOError):
            return None

        if ({u'REPLAYGAIN_TRACK_PEAK', u'REPLAYGAIN_TRACK_GAIN',
             u'REPLAYGAIN_ALBUM_PEAK', u'REPLAYGAIN_ALBUM_GAIN'}.issubset(
                [key.upper() for key in vorbis_metadata.keys()])):
            # we have ReplayGain data
            try:
                return ReplayGain(
                    vorbis_metadata[u'REPLAYGAIN_TRACK_GAIN'][0][0:-len(" dB")],
                    vorbis_metadata[u'REPLAYGAIN_TRACK_PEAK'][0],
                    vorbis_metadata[u'REPLAYGAIN_ALBUM_GAIN'][0][0:-len(" dB")],
                    vorbis_metadata[u'REPLAYGAIN_ALBUM_PEAK'][0])
            except ValueError:
                return None
        else:
            return None

    def set_replay_gain(self, replaygain):
        """given a ReplayGain object, sets the track's gain to those values

        may raise IOError if unable to modify the file"""

        if replaygain is None:
            return self.delete_replay_gain()

        metadata = self.get_metadata()

        if metadata.has_block(Flac_VORBISCOMMENT.BLOCK_ID):
            vorbis_comment = metadata.get_block(Flac_VORBISCOMMENT.BLOCK_ID)
        else:
            from audiotools import VERSION

            vorbis_comment = Flac_VORBISCOMMENT(
                [], u"Python Audio Tools {}".format(VERSION))
            metadata.add_block(vorbis_comment)

        vorbis_comment[u"REPLAYGAIN_TRACK_GAIN"] = [
            u"{:.2f} dB".format(replaygain.track_gain)]
        vorbis_comment[u"REPLAYGAIN_TRACK_PEAK"] = [
            u"{:.8f}".format(replaygain.track_peak)]
        vorbis_comment[u"REPLAYGAIN_ALBUM_GAIN"] = [
            u"{:.2f} dB".format(replaygain.album_gain)]
        vorbis_comment[u"REPLAYGAIN_ALBUM_PEAK"] = [
            u"{:.8f}".format(replaygain.album_peak)]
        vorbis_comment[u"REPLAYGAIN_REFERENCE_LOUDNESS"] = [u"89.0 dB"]

        self.update_metadata(metadata)

    def delete_replay_gain(self):
        """removes ReplayGain values from file, if any

        may raise IOError if unable to modify the file"""

        metadata = self.get_metadata()

        if metadata.has_block(Flac_VORBISCOMMENT.BLOCK_ID):
            vorbis_comment = metadata.get_block(Flac_VORBISCOMMENT.BLOCK_ID)

            for field in [u"REPLAYGAIN_TRACK_GAIN",
                          u"REPLAYGAIN_TRACK_PEAK",
                          u"REPLAYGAIN_ALBUM_GAIN",
                          u"REPLAYGAIN_ALBUM_PEAK",
                          u"REPLAYGAIN_REFERENCE_LOUDNESS"]:
                try:
                    del(vorbis_comment[field])
                except KeyError:
                    pass

            self.update_metadata(metadata)

    def clean(self, output_filename=None):
        """cleans the file of known data and metadata problems

        output_filename is an optional filename of the fixed file
        if present, a new AudioFile is written to that path
        otherwise, only a dry-run is performed and no new file is written

        return list of fixes performed as Unicode strings

        raises IOError if unable to write the file or its metadata
        raises ValueError if the file has errors of some sort
        """

        import os.path
        from audiotools.id3 import skip_id3v2_comment

        def seektable_valid(seektable, metadata_offset, input_file):
            from audiotools.bitstream import BitstreamReader
            reader = BitstreamReader(input_file, False)

            for (pcm_frame_offset,
                 seekpoint_offset,
                 pcm_frame_count) in seektable.seekpoints:
                reader.seek(seekpoint_offset + metadata_offset)
                try:
                    (sync_code,
                     reserved1,
                     reserved2) = reader.parse(
                        "14u 1u 1p 4p 4p 4p 3p 1u")
                    if (((sync_code != 0x3FFE) or
                         (reserved1 != 0) or
                         (reserved2 != 0))):
                        return False
                except IOError:
                    return False
            else:
                return True

        fixes_performed = []
        with open(self.filename, "rb") as input_f:
            # remove ID3 tags from before and after FLAC stream
            stream_size = os.path.getsize(self.filename)

            stream_offset = skip_id3v2_comment(input_f)
            if stream_offset > 0:
                from audiotools.text import CLEAN_FLAC_REMOVE_ID3V2
                fixes_performed.append(CLEAN_FLAC_REMOVE_ID3V2)
                stream_size -= stream_offset

            try:
                input_f.seek(-128, 2)
                if input_f.read(3) == b'TAG':
                    from audiotools.text import CLEAN_FLAC_REMOVE_ID3V1
                    fixes_performed.append(CLEAN_FLAC_REMOVE_ID3V1)
                    stream_size -= 128
            except IOError:
                # file isn't 128 bytes long
                pass

            if output_filename is not None:
                with open(output_filename, "wb") as output_f:
                    input_f.seek(stream_offset, 0)
                    while stream_size > 0:
                        s = input_f.read(4096)
                        if len(s) > stream_size:
                            s = s[0:stream_size]
                        output_f.write(s)
                        stream_size -= len(s)

                output_track = self.__class__(output_filename)

            metadata = self.get_metadata()
            metadata_size = metadata.size()

            # fix empty MD5SUM
            if self.__md5__ == b"\x00" * 16:
                from hashlib import md5
                from audiotools import transfer_framelist_data

                md5sum = md5()
                transfer_framelist_data(
                    self.to_pcm(),
                    md5sum.update,
                    signed=True,
                    big_endian=False)
                metadata.get_block(
                    Flac_STREAMINFO.BLOCK_ID).md5sum = md5sum.digest()
                from audiotools.text import CLEAN_FLAC_POPULATE_MD5
                fixes_performed.append(CLEAN_FLAC_POPULATE_MD5)

            # fix missing WAVEFORMATEXTENSIBLE_CHANNEL_MASK
            if (((self.channels() > 2) or
                 (self.bits_per_sample() > 16))):
                from audiotools.text import CLEAN_FLAC_ADD_CHANNELMASK

                try:
                    vorbis_comment = metadata.get_block(
                        Flac_VORBISCOMMENT.BLOCK_ID)
                except IndexError:
                    from audiotools import VERSION

                    vorbis_comment = Flac_VORBISCOMMENT(
                        [], u"Python Audio Tools {}".format(VERSION))

                if ((u"WAVEFORMATEXTENSIBLE_CHANNEL_MASK" not in
                     vorbis_comment.keys())):
                    fixes_performed.append(CLEAN_FLAC_ADD_CHANNELMASK)
                    vorbis_comment[
                        u"WAVEFORMATEXTENSIBLE_CHANNEL_MASK"] = \
                        [u"0x{:04X}".format(int(self.channel_mask()))]

                    metadata.replace_blocks(
                        Flac_VORBISCOMMENT.BLOCK_ID,
                        [vorbis_comment])

            if metadata.has_block(Flac_SEEKTABLE.BLOCK_ID):
                # fix an invalid SEEKTABLE, if necessary
                if (not seektable_valid(
                        metadata.get_block(Flac_SEEKTABLE.BLOCK_ID),
                        stream_offset + 4 + metadata_size,
                        input_f)):
                    from audiotools.text import CLEAN_FLAC_FIX_SEEKTABLE

                    fixes_performed.append(CLEAN_FLAC_FIX_SEEKTABLE)

                    metadata.replace_blocks(Flac_SEEKTABLE.BLOCK_ID,
                                            [self.seektable()])
            else:
                # add SEEKTABLE block if not present
                from audiotools.text import CLEAN_FLAC_ADD_SEEKTABLE

                fixes_performed.append(CLEAN_FLAC_ADD_SEEKTABLE)

                metadata.add_block(self.seektable())

            # fix remaining metadata problems
            # which automatically shifts STREAMINFO to the right place
            # (the message indicating the fix has already been output)
            (metadata, metadata_fixes) = metadata.clean()
            if output_filename is not None:
                output_track.update_metadata(metadata)

            return fixes_performed + metadata_fixes


class OggFlacMetaData(FlacMetaData):
    @classmethod
    def converted(cls, metadata):
        """takes a MetaData object and returns an OggFlacMetaData object"""

        if metadata is None:
            return None
        elif isinstance(metadata, FlacMetaData):
            return cls([block.copy() for block in metadata.block_list])
        else:
            return cls([Flac_VORBISCOMMENT.converted(metadata)] +
                       [Flac_PICTURE.converted(image)
                        for image in metadata.images()])

    def __repr__(self):
        return ("OggFlacMetaData({!r})".format(self.block_list))

    @classmethod
    def parse(cls, packetreader):
        """returns an OggFlacMetaData object from the given ogg.PacketReader

        raises IOError or ValueError if an error occurs reading MetaData"""

        from io import BytesIO
        from audiotools.bitstream import BitstreamReader, parse

        streaminfo = None
        applications = []
        seektable = None
        vorbis_comment = None
        cuesheet = None
        pictures = []

        (packet_byte,
         ogg_signature,
         major_version,
         minor_version,
         header_packets,
         flac_signature,
         block_type,
         block_length,
         minimum_block_size,
         maximum_block_size,
         minimum_frame_size,
         maximum_frame_size,
         sample_rate,
         channels,
         bits_per_sample,
         total_samples,
         md5sum) = parse(
            "8u 4b 8u 8u 16u 4b 8u 24u 16u 16u 24u 24u 20u 3u 5u 36U 16b",
            False,
            packetreader.read_packet())

        block_list = [Flac_STREAMINFO(minimum_block_size=minimum_block_size,
                                      maximum_block_size=maximum_block_size,
                                      minimum_frame_size=minimum_frame_size,
                                      maximum_frame_size=maximum_frame_size,
                                      sample_rate=sample_rate,
                                      channels=channels + 1,
                                      bits_per_sample=bits_per_sample + 1,
                                      total_samples=total_samples,
                                      md5sum=md5sum)]

        for i in range(header_packets):
            packet = BitstreamReader(BytesIO(packetreader.read_packet()),
                                     False)
            (block_type, length) = packet.parse("1p 7u 24u")
            if block_type == 1:    # PADDING
                block_list.append(Flac_PADDING.parse(packet, length))
            if block_type == 2:    # APPLICATION
                block_list.append(Flac_APPLICATION.parse(packet, length))
            elif block_type == 3:  # SEEKTABLE
                block_list.append(Flac_SEEKTABLE.parse(packet, length // 18))
            elif block_type == 4:  # VORBIS_COMMENT
                block_list.append(Flac_VORBISCOMMENT.parse(packet))
            elif block_type == 5:  # CUESHEET
                block_list.append(Flac_CUESHEET.parse(packet))
            elif block_type == 6:  # PICTURE
                block_list.append(Flac_PICTURE.parse(packet))
            elif (block_type >= 7) and (block_type <= 126):
                from audiotools.text import ERR_FLAC_RESERVED_BLOCK
                raise ValueError(ERR_FLAC_RESERVED_BLOCK.format(block_type))
            elif block_type == 127:
                from audiotools.text import ERR_FLAC_INVALID_BLOCK
                raise ValueError(ERR_FLAC_INVALID_BLOCK)

        return cls(block_list)

    def build(self, pagewriter, serial_number):
        """pagewriter is an ogg.PageWriter object

        returns new sequence number"""

        from audiotools.bitstream import build, BitstreamRecorder, format_size
        from audiotools.ogg import packet_to_pages

        # build extended Ogg FLAC STREAMINFO block
        # which will always occupy its own page
        streaminfo = self.get_block(Flac_STREAMINFO.BLOCK_ID)

        # all our non-STREAMINFO blocks that are small enough
        # to fit in the output stream
        valid_blocks = [b for b in self.blocks()
                        if ((b.BLOCK_ID != Flac_STREAMINFO.BLOCK_ID) and
                            (b.size() < (2 ** 24)))]

        page = next(packet_to_pages(
            build("8u 4b 8u 8u 16u " +
                  "4b 8u 24u 16u 16u 24u 24u 20u 3u 5u 36U 16b",
                  False,
                  (0x7F,
                   b"FLAC",
                   1,
                   0,
                   len(valid_blocks),
                   b"fLaC",
                   0,
                   format_size("16u 16u 24u 24u 20u 3u 5u 36U 16b") // 8,
                   streaminfo.minimum_block_size,
                   streaminfo.maximum_block_size,
                   streaminfo.minimum_frame_size,
                   streaminfo.maximum_frame_size,
                   streaminfo.sample_rate,
                   streaminfo.channels - 1,
                   streaminfo.bits_per_sample - 1,
                   streaminfo.total_samples,
                   streaminfo.md5sum)),
            bitstream_serial_number=serial_number,
            starting_sequence_number=0))

        page.stream_beginning = True
        pagewriter.write(page)

        sequence_number = 1

        # pack remaining metadata blocks into Ogg packets
        for (i, block) in enumerate(valid_blocks, 1):
            packet = BitstreamRecorder(False)
            packet.build("1u 7u 24u",
                         (0 if not (i == len(valid_blocks)) else 1,
                          block.BLOCK_ID, block.size()))
            block.build(packet)
            for page in packet_to_pages(
                    packet.data(),
                    bitstream_serial_number=serial_number,
                    starting_sequence_number=sequence_number):
                pagewriter.write(page)
                sequence_number += 1

        return sequence_number


def sizes_to_offsets(sizes):
    """takes list of (frame_size, frame_frames) tuples
    and converts it to a list of (cumulative_size, frame_frames)
    tuples"""

    current_position = 0
    offsets = []
    for frame_size, frame_frames in sizes:
        offsets.append((current_position, frame_frames))
        current_position += frame_size
    return offsets
