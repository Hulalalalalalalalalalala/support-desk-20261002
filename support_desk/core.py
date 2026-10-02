from .storage import JsonStore, text, minute, positive

PRIORITIES = ("urgent", "high", "normal", "low")

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

    def set_priority(self, ticket_id, priority):
        ticket_id, priority = text(ticket_id, "ticket_id"), text(priority, "priority")
        if priority not in PRIORITIES:
            raise ValueError("priority must be one of low, normal, high, urgent")
        return self._change(ticket_id, lambda t: t.update(priority=priority))

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
        self._write(data)
        return entry

    def search_knowledge(self, query=None):
        if query is None:
            terms = None
        elif not isinstance(query, str) or not query.strip():
            raise ValueError("query must be a nonempty string")
        else:
            terms = [term.casefold() for term in query.strip().split()]
        entries = sorted(self._read().get("knowledge", {}).values(), key=lambda entry: entry["article_id"])
        if terms is None:
            return entries
        return [entry for entry in entries
                if all(term in entry["title"].casefold() or term in entry["content"].casefold() for term in terms)]

    def list_tickets(self, status=None):
        if status not in (None, "open", "closed"):
            raise ValueError("status must be open or closed")
        return sorted((t for t in self._read().get("tickets", {}).values() if status is None or t["status"] == status), key=lambda t: t["ticket_id"])

    def priority_queue(self):
        items = []
        for ticket in self._read().get("tickets", {}).values():
            if ticket["status"] == "closed":
                continue
            items.append({"ticket": ticket, "priority": ticket.get("priority", "normal")})
        items.sort(key=lambda item: (PRIORITIES.index(item["priority"]), item["ticket"]["ticket_id"]))
        return items
