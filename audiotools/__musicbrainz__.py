#!/usr/bin/python

#Audio Tools, a module and set of tools for manipulating audio data
#Copyright (C) 2007-2009  Brian Langenberger

#This program is free software; you can redistribute it and/or modify
#it under the terms of the GNU General Public License as published by
#the Free Software Foundation; either version 2 of the License, or
#(at your option) any later version.

#This program is distributed in the hope that it will be useful,
#but WITHOUT ANY WARRANTY; without even the implied warranty of
#MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#GNU General Public License for more details.

#You should have received a copy of the GNU General Public License
#along with this program; if not, write to the Free Software
#Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301  USA

import urllib
import gettext

gettext.install("audiotools",unicode=True)

class MBDiscID:
    #tracks is a list of track lengths in CD frames
    #offsets, if present, is a list of track offsets in CD frames
    #length, if present, is the length of the entire disc in CD frames
    #lead_in is the location of the first track on the CD, in frames
    def __init__(self, tracks=[], offsets=None, length=None, lead_in=150):
        self.tracks = tracks
        self.__offsets__ = offsets
        self.__length__ = length
        self.__lead_in__ = lead_in

    def offsets(self):
        if (self.__offsets__ is None):
            offsets = [self.__lead_in__]

            for track in self.tracks[0:-1]:
                offsets.append(track + offsets[-1])

            return offsets
        else:
            return self.__offsets__

    #first_track_number, last_track_number and lead_out_track_offset are ints
    #frame_offsets is a list of ints
    #FIXME - shift these fields to the object constructor
    #returns a MusicBrainz DiscID value as a string
    def discid(self, first_track_number = None,
               last_track_number = None,
               lead_out_track_offset = None):
        from hashlib import sha1

        if (lead_out_track_offset is None):
            if (self.__length__ is None):
                lead_out_track_offset = sum(self.tracks) + self.__lead_in__
            else:
                lead_out_track_offset = self.__length__ + self.__lead_in__

        if (first_track_number is None):
            first_track_number = 1

        if (last_track_number is None):
            last_track_number = len(self.tracks)

        digest = sha1("%02X%02X%s" % \
                      (first_track_number,
                       last_track_number,
                       "".join(["%08X" % (i) for i in
                                [lead_out_track_offset] +
                                self.offsets() +
                                ([0] * (99 - len(self.offsets())))])))

        return "".join([{'=':'-','+':'.','/':'_'}.get(c,c) for c in
                        digest.digest().encode('base64').rstrip('\n')])

    def __str__(self):
        return self.discid()

class MusicBrainz:
    def __init__(self, server, port, messenger):
        self.server = server
        self.port = port
        self.connection = None
        self.messenger = messenger

    def connect(self):
        import httplib

        self.connection = httplib.HTTPConnection(self.server,self.port)

    def close(self):
        if (self.connection is not None):
            self.connection.close()

    #disc_id is a MBDiscID object
    #output is a file-like stream
    def read_data(self, disc_id, output):
        self.connection.request(
            "GET",
            "%s?%s" % ("/ws/1/release",
                       urllib.urlencode({"type":"xml","discid":str(disc_id)})))

        response = self.connection.getresponse()
        #FIXME - check for errors in the HTTP response
        output.write(response.read())


