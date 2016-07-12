#!/usr/bin/python3
# -*- coding: utf-8 -*-
#
#
# Copyright (C) 2015 Bitergia
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 59 Temple Place - Suite 330, Boston, MA 02111-1307, USA.
#
# Authors:
#   Alvaro del Castillo San Felix <acs@bitergia.com>
#

import json
import logging

from datetime import datetime
from dateutil import parser

from grimoire.elk.enrich import Enrich

class StackExchangeEnrich(Enrich):

    def __init__(self, stackexchange, sortinghat=True, db_projects_map = None):
        super().__init__(sortinghat, db_projects_map)
        self.elastic = None
        self.perceval_backend = stackexchange
        self.index_stackexchange = "stackexchange"

    def set_elastic(self, elastic):
        self.elastic = elastic

    def get_field_date(self):
        return "metadata__updated_on"

    def get_field_unique_id(self):
        return "question_id"

    def get_elastic_mappings(self):

        mapping = """
        {
            "properties": {
                "title_analyzed": {
                  "type": "string",
                  "index":"analyzed"
                  }
           }
        } """

        return {"items":mapping}

    def get_sh_identity(self, owner):
        identity = {}

        identity['username'] = owner['display_name']
        identity['email'] = None
        identity['name'] = owner['display_name']

        return identity

    def get_identities(self, item):
        """ Return the identities from an item """
        identities = []

        item = item['data']

        for identity in ['owner']:
            if identity in item and item[identity]:
                user = self.get_sh_identity(item[identity])
                identities.append(user)
            if 'answers' in item:
                for answer in item['answers']:
                    user = self.get_sh_identity(answer[identity])
                    identities.append(user)
        return identities

    def get_item_sh(self, item, identity_field):
        """ Add sorting hat enrichment fields for the author of the item """

        eitem = {}  # Item enriched

        update_date = datetime.fromtimestamp(item["last_activity_date"])

        # Add Sorting Hat fields
        if identity_field not in item:
            return eitem
        identity  = self.get_sh_identity(item[identity_field])
        eitem = self.get_item_sh_fields(identity, update_date)

        return eitem

    def get_rich_item(self, item, kind='question'):
        eitem = {}

        # Fields common in questions and answers
        common_fields = ["title", "comments_count", "question_id",
                         "creation_date", "delete_vote_count", "up_vote_count",
                         "down_vote_count","favorite_count", "view_count",
                         "last_activity_date", "link", "score", "tags"]

        if kind == 'question':
            # metadata fields to copy, only in question (perceval item)
            copy_fields = ["metadata__updated_on","metadata__timestamp","ocean-unique-id","origin"]
            for f in copy_fields:
                if f in item:
                    eitem[f] = item[f]
                else:
                    eitem[f] = None
            # The real data
            question = item['data']

            eitem["type"] = 'question'
            eitem["author"] = question['owner']['display_name']
            eitem["author_link"] = question['owner']['link']
            eitem["author_reputation"] = question['owner']['reputation']

            # data fields to copy
            copy_fields = common_fields
            for f in copy_fields:
                if f in question:
                    eitem[f] = question[f]
                else:
                    eitem[f] = None

            # Fields which names are translated
            map_fields = {"title": "question_title"
                          }
            for fn in map_fields:
                eitem[map_fields[fn]] = question[fn]


            eitem.update(self.get_grimoire_fields(item["metadata__updated_on"], "question"))

            if self.sortinghat:
                eitem.update(self.get_item_sh(question, "owner"))

        elif kind == 'answer':
            answer = item

            eitem["type"] = 'answer'
            eitem["author"] = answer['owner']['display_name']
            eitem["author_link"] = answer['owner']['link']
            eitem["author_reputation"] = answer['owner']['reputation']

            # data fields to copy
            copy_fields = common_fields + ["is_accepted", "answer_id"]
            for f in copy_fields:
                if f in answer:
                    eitem[f] = answer[f]
                else:
                    eitem[f] = None

            # Fields which names are translated
            map_fields = {"title": "question_title"
                          }
            for fn in map_fields:
                eitem[map_fields[fn]] = answer[fn]

            creation_date = datetime.fromtimestamp(item["creation_date"]).isoformat()
            eitem.update(self.get_grimoire_fields(creation_date, "answer"))

            if self.sortinghat:
                eitem.update(self.get_item_sh(answer, "owner"))

        return eitem

    def enrich_items(self, items):
        max_items = self.elastic.max_items_bulk
        current = 0
        bulk_json = ""

        url = self.elastic.index_url+'/items/_bulk'

        logging.debug("Adding items to %s (in %i packs)" % (url, max_items))

        for item in items:
            if current >= max_items:
                self.requests.put(url, data=bulk_json)
                bulk_json = ""
                current = 0

            rich_item = self.get_rich_item(item)
            data_json = json.dumps(rich_item)
            bulk_json += '{"index" : {"_id" : "%s" } }\n' % \
                (rich_item[self.get_field_unique_id()])
            bulk_json += data_json +"\n"  # Bulk document
            current += 1
            # Time to enrich also de answers
            if 'answers' in item['data']:
                for answer in item['data']['answers']:
                    rich_answer = self.get_rich_item(answer, kind='answer')
                    data_json = json.dumps(rich_answer)
                    bulk_json += '{"index" : {"_id" : "%i_%i" } }\n' % \
                        (rich_answer[self.get_field_unique_id()],
                         rich_answer['answer_id'])
                    bulk_json += data_json +"\n"  # Bulk document
                    current += 1

        self.requests.put(url, data = bulk_json)
