from .storage import JsonStore, text, minute, positive

PRIORITIES = ("urgent", "high", "normal", "low")
PRIORITY_RANK = {name: index for index, name in enumerate(PRIORITIES)}

class SupportDesk(JsonStore):
    def open_ticket(self, ticket_id, customer, subject, opened_at=None):
        ticket_id, customer, subject = text(ticket_id, "ticket_id"), text(customer, "customer"), text(subject, "subject")
        if opened_at is not None:
            opened_at = minute(opened_at, "opened_at")
        data = self._read()
        tickets = data.setdefault("tickets", {})
        if ticket_id in tickets:
            raise ValueError("ticket already exists")
        ticket = {"ticket_id": ticket_id, "customer": customer, "subject": subject, "status": "open", "assignee": None, "notes": [], "resolution": None}
        if opened_at is not None:
            ticket["opened_at"] = opened_at
            ticket["first_response"] = None
        tickets[ticket_id] = ticket
        self._write(data)
        return ticket

    def get(self, ticket_id):
        ticket = self._read().get("tickets", {}).get(ticket_id)
        if ticket is None:
            raise ValueError("unknown ticket")
        return ticket

    def _change(self, ticket_id, operation):
        data = self._read()
        ticket = data.get("tickets", {}).get(ticket_id)
        if ticket is None or ticket["status"] == "closed":
            raise ValueError("ticket must exist and be open")
        operation(ticket)
        self._write(data)
        return ticket

    def assign(self, ticket_id, assignee):
        assignee = text(assignee, "assignee")
        return self._change(ticket_id, lambda t: t.update(assignee=assignee))

    def note(self, ticket_id, message):
        message = text(message, "message")
        return self._change(ticket_id, lambda t: t["notes"].append(message))

    def close(self, ticket_id, resolution):
        resolution = text(resolution, "resolution")
        def apply(ticket):
            if not ticket["assignee"]:
                raise ValueError("assign the ticket before closing")
            ticket.update(status="closed", resolution=resolution)
        return self._change(ticket_id, apply)

    def reopen_ticket(self, ticket_id, reason):
        ticket_id, reason = text(ticket_id, "ticket_id"), text(reason, "reason")
        data = self._read()
        ticket = data.get("tickets", {}).get(ticket_id)
        if ticket is None or ticket["status"] != "closed":
            raise ValueError("ticket must exist and be closed")
        ticket.setdefault("reopen_history", []).append({"reason": reason, "resolution": ticket["resolution"]})
        ticket.update(status="open", resolution=None)
        self._write(data)
        return ticket

    def priority_queue(self):
        items = [{"ticket": ticket, "priority": ticket.get("priority", "normal")}
                 for ticket in self._read().get("tickets", {}).values()
                 if ticket["status"] != "closed"]
        items.sort(key=lambda item: (PRIORITY_RANK[item["priority"]], item["ticket"]["ticket_id"]))
        return items

    def set_priority(self, ticket_id, priority):
        ticket_id, priority = text(ticket_id, "ticket_id"), text(priority, "priority")
        if priority not in PRIORITY_RANK:
            raise ValueError("priority must be one of low, normal, high, urgent")
        data = self._read()
        ticket = data.get("tickets", {}).get(ticket_id)
        if ticket is None or ticket["status"] == "closed":
            raise ValueError("ticket must exist and be open")
        ticket["priority"] = priority
        self._write(data)
        return ticket

    def respond(self, ticket_id, message, responded_at):
        ticket_id, message = text(ticket_id, "ticket_id"), text(message, "message")
        responded_at = minute(responded_at, "responded_at")
        data = self._read()
        ticket = data.get("tickets", {}).get(ticket_id)
        if ticket is None or ticket["status"] == "closed":
            raise ValueError("ticket must exist and be open")
        if "opened_at" not in ticket:
            raise ValueError("ticket has no opened_at")
        if ticket.get("first_response") is not None:
            raise ValueError("ticket already has a first response")
        if responded_at < ticket["opened_at"]:
            raise ValueError("responded_at must not be earlier than opened_at")
        ticket["first_response"] = {"message": message, "responded_at": responded_at}
        self._write(data)
        return ticket

    def respond_with_knowledge(self, ticket_id, article_id, responded_at=None):
        ticket_id, article_id = text(ticket_id, "ticket_id"), text(article_id, "article_id")
        responded_at = minute(responded_at, "responded_at")
        data = self._read()
        ticket = data.get("tickets", {}).get(ticket_id)
        if ticket is None or ticket["status"] == "closed":
            raise ValueError("ticket must exist and be open")
        if "opened_at" not in ticket:
            raise ValueError("ticket has no opened_at")
        if ticket.get("first_response") is not None:
            raise ValueError("ticket already has a first response")
        entry = data.get("knowledge", {}).get(article_id)
        if entry is None:
            raise ValueError("unknown knowledge article")
        if not data.get("knowledge_enabled", {}).get(article_id, True):
            raise ValueError("knowledge article is disabled")
        if responded_at < ticket["opened_at"]:
            raise ValueError("responded_at must not be earlier than opened_at")
        ticket["first_response"] = {
            "message": entry["content"],
            "responded_at": responded_at,
            "knowledge": dict(entry),
        }
        self._write(data)
        return ticket

    def response_stats(self):
        tickets = list(self._read().get("tickets", {}).values())
        timed = [t for t in tickets if "opened_at" in t]
        responded = [t for t in timed if t.get("first_response") is not None]
        durations = [t["first_response"]["responded_at"] - t["opened_at"] for t in responded]
        return {
            "timed": len(timed),
            "responded": len(responded),
            "pending": len(timed) - len(responded),
            "untimed": len(tickets) - len(timed),
            "average_minutes": sum(durations) / len(durations) if durations else None,
            "max_minutes": max(durations) if durations else None,
        }

    def response_queue(self, as_of, target_minutes=30):
        as_of = minute(as_of, "as_of")
        target_minutes = positive(target_minutes, "target_minutes")
        items = []
        untimed = 0
        for ticket in self._read().get("tickets", {}).values():
            if ticket["status"] == "closed" or ticket.get("first_response") is not None:
                continue
            if "opened_at" not in ticket:
                untimed += 1
                continue
            if ticket["opened_at"] > as_of:
                raise ValueError("opened_at must not be later than as_of")
            waiting = as_of - ticket["opened_at"]
            items.append({"ticket": ticket, "waiting_minutes": waiting, "overdue": waiting > target_minutes})
        items.sort(key=lambda item: (item["ticket"]["opened_at"], item["ticket"]["ticket_id"]))
        return {"untimed": untimed, "items": items}

    def response_target_report(self, as_of, targets=None):
        as_of = minute(as_of, "as_of")
        target_minutes = {"urgent": 5, "high": 15, "normal": 30, "low": 60}
        if targets is not None:
            if not isinstance(targets, dict):
                raise ValueError("targets must be an object or null")
            for priority, value in targets.items():
                if priority not in PRIORITY_RANK:
                    raise ValueError("targets has an unknown priority: " + str(priority))
                target_minutes[priority] = positive(value, "targets." + priority)
        tickets = list(self._read().get("tickets", {}).values())
        for ticket in tickets:
            if "opened_at" not in ticket:
                continue
            if ticket["opened_at"] > as_of:
                raise ValueError("opened_at must not be later than as_of")
            response = ticket.get("first_response")
            if response is not None and response["responded_at"] > as_of:
                raise ValueError("responded_at must not be later than as_of")
        groups = []
        for priority in PRIORITIES:
            target = target_minutes[priority]
            counts = {"responded": 0, "on_time": 0, "late": 0, "pending": 0,
                      "overdue": 0, "untimed": 0, "closed_without_response": 0}
            for ticket in tickets:
                if ticket.get("priority", "normal") != priority:
                    continue
                if "opened_at" not in ticket:
                    counts["untimed"] += 1
                    continue
                response = ticket.get("first_response")
                if response is not None:
                    counts["responded"] += 1
                    if response["responded_at"] - ticket["opened_at"] <= target:
                        counts["on_time"] += 1
                    else:
                        counts["late"] += 1
                elif ticket["status"] == "closed":
                    counts["closed_without_response"] += 1
                else:
                    counts["pending"] += 1
                    if as_of - ticket["opened_at"] > target:
                        counts["overdue"] += 1
            rate = counts["on_time"] / counts["responded"] if counts["responded"] else None
            groups.append({"priority": priority, "target_minutes": target, **counts, "on_time_rate": rate})
        return {"as_of": as_of, "groups": groups}

    def publish_knowledge(self, article_id, ticket_id):
        article_id, ticket_id = text(article_id, "article_id"), text(ticket_id, "ticket_id")
        data = self._read()
        knowledge = data.get("knowledge", {})
        if article_id in knowledge:
            raise ValueError("article already exists")
        if any(entry["source_ticket_id"] == ticket_id for entry in knowledge.values()):
            raise ValueError("ticket already published")
        ticket = data.get("tickets", {}).get(ticket_id)
        if ticket is None or ticket["status"] != "closed":
            raise ValueError("source ticket must exist and be closed")
        entry = {"article_id": article_id, "source_ticket_id": ticket_id, "title": ticket["subject"], "content": ticket["resolution"]}
        data.setdefault("knowledge", {})[article_id] = entry
        data.setdefault("knowledge_history", {})[article_id] = [
            {"revision": 1, "title": entry["title"], "content": entry["content"]}
        ]
        self._write(data)
        return entry

    def _append_revision(self, data, article_id, entry, title, content):
        histories = data.setdefault("knowledge_history", {})
        history = histories.get(article_id)
        if history is None:
            # Articles written before history existed keep their current content as revision 1.
            history = [{"revision": 1, "title": entry["title"], "content": entry["content"]}]
            histories[article_id] = history
        history.append({"revision": history[-1]["revision"] + 1, "title": title, "content": content})
        entry["title"] = title
        entry["content"] = content

    def update_knowledge(self, article_id, title, content):
        article_id, title, content = (text(article_id, "article_id"),
                                      text(title, "title"),
                                      text(content, "content"))
        data = self._read()
        entry = data.get("knowledge", {}).get(article_id)
        if entry is None:
            raise ValueError("unknown knowledge article")
        if title != entry["title"] or content != entry["content"]:
            self._append_revision(data, article_id, entry, title, content)
            self._write(data)
        return entry

    def knowledge_history(self, article_id):
        article_id = text(article_id, "article_id")
        data = self._read()
        entry = data.get("knowledge", {}).get(article_id)
        if entry is None:
            raise ValueError("unknown knowledge article")
        history = data.get("knowledge_history", {}).get(article_id)
        if history is None:
            # Old articles without stored history use their current content as revision 1;
            # the missing record is never reconstructed or backfilled.
            history = [{"revision": 1, "title": entry["title"], "content": entry["content"]}]
        return [{"revision": item["revision"], "title": item["title"], "content": item["content"]}
                for item in history]

    def restore_knowledge(self, article_id, revision):
        article_id = text(article_id, "article_id")
        revision = positive(revision, "revision")
        data = self._read()
        entry = data.get("knowledge", {}).get(article_id)
        if entry is None:
            raise ValueError("unknown knowledge article")
        history = data.get("knowledge_history", {}).get(article_id)
        if history is None:
            source = {"revision": 1, "title": entry["title"], "content": entry["content"]} if revision == 1 else None
        else:
            source = next((item for item in history if item["revision"] == revision), None)
        if source is None:
            raise ValueError("unknown knowledge revision")
        if source["title"] != entry["title"] or source["content"] != entry["content"]:
            self._append_revision(data, article_id, entry, source["title"], source["content"])
            self._write(data)
        return entry

    def search_knowledge(self, query=None):
        if query is None:
            terms = None
        elif not isinstance(query, str) or not query.strip():
            raise ValueError("query must be a nonempty string")
        else:
            terms = [term.casefold() for term in query.strip().split()]
        data = self._read()
        states = data.get("knowledge_enabled", {})
        entries = sorted((entry for entry in data.get("knowledge", {}).values()
                          if states.get(entry["article_id"], True)),
                         key=lambda entry: entry["article_id"])
        if terms is None:
            return entries
        return [entry for entry in entries
                if all(term in entry["title"].casefold() or term in entry["content"].casefold() for term in terms)]

    def set_knowledge_enabled(self, article_id, enabled):
        article_id = text(article_id, "article_id")
        if type(enabled) is not bool:
            raise ValueError("enabled must be a boolean")
        data = self._read()
        entry = data.get("knowledge", {}).get(article_id)
        if entry is None:
            raise ValueError("unknown knowledge article")
        data.setdefault("knowledge_enabled", {})[article_id] = enabled
        self._write(data)
        return {"article": entry, "enabled": enabled}

    def list_tickets(self, status=None):
        if status not in (None, "open", "closed"):
            raise ValueError("status must be open or closed")
        return sorted((t for t in self._read().get("tickets", {}).values() if status is None or t["status"] == status), key=lambda t: t["ticket_id"])

    def set_category(self, ticket_id, category):
        ticket_id = text(ticket_id, "ticket_id")
        if category is not None:
            category = text(category, "category")
        data = self._read()
        ticket = data.get("tickets", {}).get(ticket_id)
        if ticket is None or ticket["status"] == "closed":
            raise ValueError("ticket must exist and be open")
        if category is None:
            ticket.pop("category", None)
        else:
            ticket["category"] = category
        self._write(data)
        return ticket

    def list_by_category(self, category, status=None):
        if category is not None:
            category = text(category, "category")
        if status not in (None, "open", "closed"):
            raise ValueError("status must be open or closed")
        tickets = self._read().get("tickets", {}).values()
        if category is None:
            matches = (t for t in tickets if t.get("category") is None)
        else:
            matches = (t for t in tickets if t.get("category") == category)
        return sorted((t for t in matches if status is None or t["status"] == status), key=lambda t: t["ticket_id"])
