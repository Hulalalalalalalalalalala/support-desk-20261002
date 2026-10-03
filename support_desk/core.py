from .storage import JsonStore, text, minute, positive

PRIORITIES = ("urgent", "high", "normal", "low")
PRIORITY_RANK = {name: index for index, name in enumerate(PRIORITIES)}

_UNSET = object()

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

    def transfer_ticket(self, ticket_id, assignee, reason, transferred_at):
        ticket_id, assignee, reason = (text(ticket_id, "ticket_id"),
                                       text(assignee, "assignee"),
                                       text(reason, "reason"))
        transferred_at = minute(transferred_at, "transferred_at")
        data = self._read()
        ticket = data.get("tickets", {}).get(ticket_id)
        if ticket is None or ticket["status"] != "open" or not ticket.get("assignee"):
            raise ValueError("ticket must exist, be open and assigned")
        if assignee == ticket["assignee"]:
            raise ValueError("assignee must differ from the current assignee")
        if "opened_at" in ticket and transferred_at < ticket["opened_at"]:
            raise ValueError("transferred_at must not be earlier than opened_at")
        history = ticket.get("transfer_history")
        if history and transferred_at < history[-1]["transferred_at"]:
            raise ValueError("transferred_at must not be earlier than the last transfer")
        history = ticket.setdefault("transfer_history", [])
        history.append({"from_assignee": ticket["assignee"], "to_assignee": assignee,
                        "reason": reason, "transferred_at": transferred_at})
        ticket["assignee"] = assignee
        self._write(data)
        return ticket

    def handover(self, source_assignee, assignees, reason, transferred_at, max_open=5):
        source_assignee = text(source_assignee, "source_assignee")
        reason = text(reason, "reason")
        if not isinstance(assignees, list) or not assignees:
            raise ValueError("assignees must be a nonempty array")
        names = []
        for element in assignees:
            if not isinstance(element, str) or not element.strip():
                raise ValueError("assignees elements must be nonblank strings")
            names.append(element.strip())
        if len(set(names)) != len(names):
            raise ValueError("assignees must not contain duplicate names")
        if source_assignee in names:
            raise ValueError("assignees must not contain the source assignee")
        transferred_at = minute(transferred_at, "transferred_at")
        max_open = positive(max_open, "max_open")
        data = self._read()
        tickets = data.get("tickets", {})
        def candidate_key(ticket):
            opened_at = ticket.get("opened_at")
            return (PRIORITY_RANK[ticket.get("priority", "normal")],
                    opened_at is None, opened_at if opened_at is not None else 0,
                    ticket["ticket_id"])
        selected = sorted((ticket for ticket in tickets.values()
                           if ticket["status"] == "open" and ticket.get("assignee") == source_assignee),
                          key=candidate_key)
        if not selected:
            return []
        loads = {name: 0 for name in names}
        for ticket in tickets.values():
            if ticket["status"] == "open" and ticket.get("assignee") in loads:
                loads[ticket["assignee"]] += 1
        plan = []
        for ticket in selected:
            available = [name for name in names if loads[name] < max_open]
            if not available:
                raise ValueError("assignees do not have enough capacity for all selected tickets")
            chosen = min(available, key=lambda name: (loads[name], name))
            loads[chosen] += 1
            plan.append((ticket, chosen))
        for ticket, chosen in plan:
            if "opened_at" in ticket and transferred_at < ticket["opened_at"]:
                raise ValueError("transferred_at must not be earlier than opened_at")
            history = ticket.get("transfer_history")
            if history and transferred_at < history[-1]["transferred_at"]:
                raise ValueError("transferred_at must not be earlier than the last transfer")
        handed_over = []
        for ticket, chosen in plan:
            ticket.setdefault("transfer_history", []).append(
                {"from_assignee": ticket["assignee"], "to_assignee": chosen,
                 "reason": reason, "transferred_at": transferred_at})
            ticket["assignee"] = chosen
            handed_over.append(ticket)
        self._write(data)
        return handed_over

    def note(self, ticket_id, message):
        message = text(message, "message")
        return self._change(ticket_id, lambda t: t["notes"].append(message))

    def close(self, ticket_id, resolution, closed_at=None):
        resolution = text(resolution, "resolution")
        if closed_at is not None:
            closed_at = minute(closed_at, "closed_at")
        def apply(ticket):
            if not ticket["assignee"]:
                raise ValueError("assign the ticket before closing")
            if closed_at is not None:
                earlier = []
                if "opened_at" in ticket:
                    earlier.append(ticket["opened_at"])
                response = ticket.get("first_response")
                if isinstance(response, dict) and response.get("responded_at") is not None:
                    earlier.append(response["responded_at"])
                earlier.extend(reply["replied_at"] for reply in ticket.get("replies") or [])
                earlier.extend(message["received_at"] for message in ticket.get("customer_messages") or [])
                earlier.extend(entry["transferred_at"] for entry in ticket.get("transfer_history") or [])
                earlier.extend(entry["closed_at"] for entry in ticket.get("reopen_history") or []
                               if "closed_at" in entry)
                if earlier and closed_at < max(earlier):
                    raise ValueError("closed_at must not be earlier than existing ticket times")
            ticket.update(status="closed", resolution=resolution)
            if closed_at is not None:
                ticket["closed_at"] = closed_at
        return self._change(ticket_id, apply)

    def reopen_ticket(self, ticket_id, reason):
        ticket_id, reason = text(ticket_id, "ticket_id"), text(reason, "reason")
        data = self._read()
        ticket = data.get("tickets", {}).get(ticket_id)
        if ticket is None or ticket["status"] != "closed":
            raise ValueError("ticket must exist and be closed")
        record = {"reason": reason, "resolution": ticket["resolution"]}
        if "closed_at" in ticket:
            record["closed_at"] = ticket.pop("closed_at")
        ticket.setdefault("reopen_history", []).append(record)
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

    def _knowledge_snapshot(self, data, article_id, revision):
        entry = data.get("knowledge", {}).get(article_id)
        if entry is None:
            raise ValueError("unknown knowledge article")
        if not data.get("knowledge_enabled", {}).get(article_id, True):
            raise ValueError("knowledge article is disabled")
        if revision is None:
            return dict(entry), None
        history = data.get("knowledge_history", {}).get(article_id)
        if history is None:
            # Articles written before history existed keep their current content as revision 1;
            # the missing history is never reconstructed or backfilled.
            source = ({"revision": 1, "title": entry["title"], "content": entry["content"]}
                      if revision == 1 else None)
        else:
            source = next((item for item in history if item["revision"] == revision), None)
        if source is None:
            raise ValueError("unknown knowledge revision")
        snapshot = {"article_id": entry["article_id"], "source_ticket_id": entry["source_ticket_id"],
                    "title": source["title"], "content": source["content"]}
        return snapshot, revision

    def respond_with_knowledge(self, ticket_id, article_id, responded_at=None, revision=None):
        ticket_id, article_id = text(ticket_id, "ticket_id"), text(article_id, "article_id")
        responded_at = minute(responded_at, "responded_at")
        if revision is not None:
            revision = positive(revision, "revision")
        data = self._read()
        ticket = data.get("tickets", {}).get(ticket_id)
        if ticket is None or ticket["status"] == "closed":
            raise ValueError("ticket must exist and be open")
        if "opened_at" not in ticket:
            raise ValueError("ticket has no opened_at")
        if ticket.get("first_response") is not None:
            raise ValueError("ticket already has a first response")
        snapshot, chosen_revision = self._knowledge_snapshot(data, article_id, revision)
        if responded_at < ticket["opened_at"]:
            raise ValueError("responded_at must not be earlier than opened_at")
        record = {
            "message": snapshot["content"],
            "responded_at": responded_at,
            "knowledge": snapshot,
        }
        if chosen_revision is not None:
            record["knowledge_revision"] = chosen_revision
        ticket["first_response"] = record
        self._write(data)
        return ticket

    def _reply_target(self, data, ticket_id):
        ticket = data.get("tickets", {}).get(ticket_id)
        if ticket is None or ticket["status"] == "closed":
            raise ValueError("ticket must exist and be open")
        if "opened_at" not in ticket:
            raise ValueError("ticket has no opened_at")
        if ticket.get("first_response") is None:
            raise ValueError("ticket has no first response")
        return ticket

    def _check_reply_time(self, ticket, replied_at):
        if replied_at < ticket["first_response"]["responded_at"]:
            raise ValueError("replied_at must not be earlier than the first response")
        replies = ticket.get("replies")
        if replies and replied_at < replies[-1]["replied_at"]:
            raise ValueError("replied_at must not be earlier than the last reply")

    def reply(self, ticket_id, message, replied_at):
        ticket_id, message = text(ticket_id, "ticket_id"), text(message, "message")
        replied_at = minute(replied_at, "replied_at")
        data = self._read()
        ticket = self._reply_target(data, ticket_id)
        self._check_reply_time(ticket, replied_at)
        ticket.setdefault("replies", []).append({"message": message, "replied_at": replied_at})
        self._write(data)
        return ticket

    def reply_with_knowledge(self, ticket_id, article_id, replied_at, revision=None):
        ticket_id, article_id = text(ticket_id, "ticket_id"), text(article_id, "article_id")
        replied_at = minute(replied_at, "replied_at")
        if revision is not None:
            revision = positive(revision, "revision")
        data = self._read()
        ticket = self._reply_target(data, ticket_id)
        snapshot, chosen_revision = self._knowledge_snapshot(data, article_id, revision)
        self._check_reply_time(ticket, replied_at)
        record = {
            "message": snapshot["content"],
            "replied_at": replied_at,
            "knowledge": snapshot,
        }
        if chosen_revision is not None:
            record["knowledge_revision"] = chosen_revision
        ticket.setdefault("replies", []).append(record)
        self._write(data)
        return ticket

    def receive(self, ticket_id, message, received_at):
        ticket_id, message = text(ticket_id, "ticket_id"), text(message, "message")
        received_at = minute(received_at, "received_at")
        data = self._read()
        ticket = data.get("tickets", {}).get(ticket_id)
        if ticket is None or ticket["status"] != "open" or "opened_at" not in ticket:
            raise ValueError("ticket must exist, be open and have opened_at")
        if received_at < ticket["opened_at"]:
            raise ValueError("received_at must not be earlier than opened_at")
        messages = ticket.get("customer_messages")
        if messages and received_at < messages[-1]["received_at"]:
            raise ValueError("received_at must not be earlier than the last customer message")
        ticket.setdefault("customer_messages", []).append(
            {"message": message, "received_at": received_at})
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

    def closure_report(self, as_of):
        as_of = minute(as_of, "as_of")
        closed = [ticket for ticket in self._read().get("tickets", {}).values()
                  if ticket["status"] == "closed"]
        for ticket in closed:
            opened_at = ticket.get("opened_at")
            if opened_at is not None and opened_at > as_of:
                raise ValueError("opened_at must not be later than as_of")
            closed_at = ticket.get("closed_at")
            if closed_at is not None and closed_at > as_of:
                raise ValueError("closed_at must not be later than as_of")
        durations = [ticket["closed_at"] - ticket["opened_at"] for ticket in closed
                     if "opened_at" in ticket and "closed_at" in ticket]
        return {
            "as_of": as_of,
            "total": len(closed),
            "timed": len(durations),
            "untimed": len(closed) - len(durations),
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

    def follow_up_queue(self, as_of, target_minutes=60):
        as_of = minute(as_of, "as_of")
        target_minutes = positive(target_minutes, "target_minutes")
        entries = []
        for ticket in self._read().get("tickets", {}).values():
            if ticket["status"] != "open":
                continue
            response = ticket.get("first_response")
            # Only a non-empty first-response object qualifies; missing, null or {} are excluded.
            if not isinstance(response, dict) or not response:
                continue
            replies = ticket.get("replies")
            if replies:
                last_answered_at = replies[-1]["replied_at"]
            else:
                last_answered_at = response["responded_at"]
            if response["responded_at"] > as_of or any(reply["replied_at"] > as_of for reply in replies or []):
                raise ValueError("response time must not be later than as_of")
            entries.append((ticket, ticket.get("priority", "normal"), last_answered_at))
        items = []
        for ticket, priority, last_answered_at in entries:
            waiting = as_of - last_answered_at
            items.append({"ticket": ticket, "priority": priority, "last_answered_at": last_answered_at,
                          "waiting_minutes": waiting, "overdue": waiting > target_minutes})
        items.sort(key=lambda item: (item["last_answered_at"], PRIORITY_RANK[item["priority"]],
                                     item["ticket"]["ticket_id"]))
        return {"as_of": as_of, "items": items}

    def customer_queue(self, as_of, target_minutes=30):
        as_of = minute(as_of, "as_of")
        target_minutes = positive(target_minutes, "target_minutes")
        entries = []
        for ticket in self._read().get("tickets", {}).values():
            if ticket["status"] != "open":
                continue
            messages = ticket.get("customer_messages")
            # Only open tickets with a follow-up history participate.
            if not messages:
                continue
            response = ticket.get("first_response")
            # Missing, null or an empty object means there is no first response.
            response_at = response["responded_at"] if isinstance(response, dict) and response else None
            answer_times = ([response_at] if response_at is not None else [])
            answer_times.extend(reply["replied_at"] for reply in ticket.get("replies") or [])
            # Any follow-up or answer later than as_of invalidates the whole query.
            if any(message["received_at"] > as_of for message in messages) or \
                    any(time > as_of for time in answer_times):
                raise ValueError("customer message or response time must not be later than as_of")
            boundary = max(answer_times) if answer_times else None
            # A follow-up in the same minute as an answer is already covered;
            # only strictly later times remain unanswered.
            unanswered = [message for message in messages
                          if boundary is None or message["received_at"] > boundary]
            if not unanswered:
                continue
            earliest = min(message["received_at"] for message in unanswered)
            waiting = as_of - earliest
            entries.append((earliest, ticket, len(unanswered), waiting))
        entries.sort(key=lambda entry: (entry[0], entry[1]["ticket_id"]))
        return [{"ticket": ticket, "count": count, "waiting_minutes": waiting,
                 "overdue": waiting > target_minutes}
                for earliest, ticket, count, waiting in entries]

    def customer_response_report(self, as_of):
        as_of = minute(as_of, "as_of")
        tickets = list(self._read().get("tickets", {}).values())
        # Only tickets with a follow-up history participate; open and closed tickets alike.
        participants = [ticket for ticket in tickets if ticket.get("customer_messages")]
        answers = {}
        for ticket in participants:
            response = ticket.get("first_response")
            # Missing, null or an empty object means there is no first response;
            # handwritten and knowledge answers are collected the same way.
            times = [response["responded_at"]] if isinstance(response, dict) and response else []
            times.extend(reply["replied_at"] for reply in ticket.get("replies") or [])
            # Any follow-up or answer later than as_of invalidates the whole query.
            if any(message["received_at"] > as_of for message in ticket["customer_messages"]) or \
                    any(time > as_of for time in times):
                raise ValueError("customer message or response time must not be later than as_of")
            answers[ticket["ticket_id"]] = times
        items = []
        for ticket in participants:
            ticket_id = ticket["ticket_id"]
            for index, message in enumerate(ticket["customer_messages"]):
                received_at = message["received_at"]
                # Matching looks at time only: the earliest answer not earlier than
                # the follow-up covers it, regardless of call order; the same answer
                # may cover several follow-ups and equal times take zero minutes.
                candidates = [time for time in answers[ticket_id] if time >= received_at]
                if candidates:
                    answered_at = min(candidates)
                    items.append({"ticket_id": ticket_id, "index": index,
                                  "received_at": received_at, "answered_at": answered_at,
                                  "response_minutes": answered_at - received_at,
                                  "outcome": "answered"})
                else:
                    outcome = "pending" if ticket["status"] != "closed" else "closed_without_answer"
                    items.append({"ticket_id": ticket_id, "index": index,
                                  "received_at": received_at, "answered_at": None,
                                  "response_minutes": None, "outcome": outcome})
        items.sort(key=lambda item: (item["received_at"], item["ticket_id"], item["index"]))
        durations = [item["response_minutes"] for item in items if item["outcome"] == "answered"]
        summary = {
            "total": len(items),
            "answered": sum(1 for item in items if item["outcome"] == "answered"),
            "pending": sum(1 for item in items if item["outcome"] == "pending"),
            "closed_without_answer": sum(1 for item in items
                                         if item["outcome"] == "closed_without_answer"),
            "average_minutes": sum(durations) / len(durations) if durations else None,
            "max_minutes": max(durations) if durations else None,
        }
        return {"as_of": as_of, "summary": summary, "items": items}

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

    def assignee_workload_report(self, as_of, response_minutes=30, follow_up_minutes=60):
        as_of = minute(as_of, "as_of")
        response_minutes = positive(response_minutes, "response_minutes")
        follow_up_minutes = positive(follow_up_minutes, "follow_up_minutes")
        groups = {}
        def bucket(assignee):
            return groups.setdefault(assignee, {"assignee": assignee, "open": 0, "pending": 0,
                                               "overdue": 0, "follow_up": 0,
                                               "follow_up_overdue": 0, "untimed": 0})
        for ticket in self._read().get("tickets", {}).values():
            if ticket["status"] != "open":
                continue
            counts = bucket(ticket.get("assignee"))
            counts["open"] += 1
            response = ticket.get("first_response")
            # Only a non-empty first-response object counts as responded; missing, null or {} are pending.
            if not isinstance(response, dict) or not response:
                if "opened_at" in ticket:
                    if ticket["opened_at"] > as_of:
                        raise ValueError("opened_at must not be later than as_of")
                    counts["pending"] += 1
                    if as_of - ticket["opened_at"] > response_minutes:
                        counts["overdue"] += 1
                else:
                    counts["untimed"] += 1
            else:
                counts["follow_up"] += 1
                replies = ticket.get("replies")
                if replies:
                    last_answered_at = replies[-1]["replied_at"]
                else:
                    last_answered_at = response["responded_at"]
                if response["responded_at"] > as_of or any(reply["replied_at"] > as_of for reply in replies or []):
                    raise ValueError("response time must not be later than as_of")
                if as_of - last_answered_at > follow_up_minutes:
                    counts["follow_up_overdue"] += 1
        ordered = [groups[name] for name in sorted(n for n in groups if n is not None)]
        if None in groups:
            ordered.insert(0, groups[None])
        return {"as_of": as_of, "groups": ordered}

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

    def knowledge_usage_report(self, since=0, until=None):
        since = minute(since, "since")
        if until is not None:
            until = minute(until, "until")
            if until < since:
                raise ValueError("until must not be earlier than since")
        data = self._read()
        usage = {}
        def record(article_id, ticket_id, at, kind):
            if at < since or (until is not None and at >= until):
                return
            stats = usage.setdefault(article_id, {"first_responses": 0, "replies": 0, "tickets": set(), "last_used_at": None})
            stats[kind] += 1
            stats["tickets"].add(ticket_id)
            if stats["last_used_at"] is None or at > stats["last_used_at"]:
                stats["last_used_at"] = at
        for ticket in data.get("tickets", {}).values():
            response = ticket.get("first_response")
            if response is not None and response.get("knowledge") is not None:
                record(response["knowledge"]["article_id"], ticket["ticket_id"], response["responded_at"], "first_responses")
            for reply in ticket.get("replies") or []:
                if reply.get("knowledge") is not None:
                    record(reply["knowledge"]["article_id"], ticket["ticket_id"], reply["replied_at"], "replies")
        states = data.get("knowledge_enabled", {})
        report = []
        for article_id in sorted(data.get("knowledge", {})):
            stats = usage.get(article_id)
            report.append({
                "article": data["knowledge"][article_id],
                "enabled": states.get(article_id, True),
                "first_responses": stats["first_responses"] if stats else 0,
                "replies": stats["replies"] if stats else 0,
                "ticket_count": len(stats["tickets"]) if stats else 0,
                "last_used_at": stats["last_used_at"] if stats else None,
            })
        return report

    def _stale_references(self, ticket, entry, article_id):
        def stale(snapshot):
            # Only the saved title/content are compared; revision, source and enabled state are not.
            return snapshot["title"] != entry["title"] or snapshot["content"] != entry["content"]
        references = []
        response = ticket.get("first_response")
        # Missing, null or empty first responses carry no reference.
        if isinstance(response, dict):
            knowledge = response.get("knowledge")
            if knowledge is not None and knowledge["article_id"] == article_id and stale(knowledge):
                references.append({"kind": "first_response", "index": None})
        for index, reply in enumerate(ticket.get("replies") or []):
            knowledge = reply.get("knowledge")
            if knowledge is not None and knowledge["article_id"] == article_id and stale(knowledge):
                references.append({"kind": "reply", "index": index})
        return references

    def _review_key(self, reference):
        if reference["kind"] == "first_response":
            return "first_response"
        return "reply:" + str(reference["index"])

    def review_knowledge(self, ticket_id, article_id):
        ticket_id, article_id = text(ticket_id, "ticket_id"), text(article_id, "article_id")
        data = self._read()
        ticket = data.get("tickets", {}).get(ticket_id)
        if ticket is None or ticket["status"] != "open":
            raise ValueError("ticket must exist and be open")
        entry = data.get("knowledge", {}).get(article_id)
        if entry is None:
            raise ValueError("unknown knowledge article")
        references = self._stale_references(ticket, entry, article_id)
        if references:
            # A confirmation stamps the article's current title/content per covered
            # reference; it stays valid only while the current content still matches.
            confirmations = (data.setdefault("knowledge_reviews", {})
                             .setdefault(ticket_id, {}).setdefault(article_id, {}))
            for reference in references:
                confirmations[self._review_key(reference)] = {"title": entry["title"],
                                                              "content": entry["content"]}
            self._write(data)
        return {"ticket_id": ticket_id, "article_id": article_id,
                "reviewed_count": len(references)}

    def knowledge_review_queue(self, article_id, offset=0, limit=20, pending_only=False):
        article_id = text(article_id, "article_id")
        # bool is a subclass of int, so compare types explicitly; floats and strings are rejected too.
        if type(offset) is not int or offset < 0:
            raise ValueError("offset must be a nonnegative integer")
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("limit must be an integer between 1 and 100")
        if type(pending_only) is not bool:
            raise ValueError("pending_only must be a boolean")
        data = self._read()
        entry = data.get("knowledge", {}).get(article_id)
        if entry is None:
            raise ValueError("unknown knowledge article")
        reviews = data.get("knowledge_reviews", {})
        current = {"title": entry["title"], "content": entry["content"]}
        matched = []
        for ticket in data.get("tickets", {}).values():
            if ticket["status"] != "open":
                continue
            references = self._stale_references(ticket, entry, article_id)
            if pending_only:
                confirmations = reviews.get(ticket["ticket_id"], {}).get(article_id, {})
                references = [reference for reference in references
                              if confirmations.get(self._review_key(reference)) != current]
            if references:
                matched.append({"ticket": ticket, "references": references})
        matched.sort(key=lambda item: item["ticket"]["ticket_id"])
        return {"total": len(matched), "items": matched[offset:offset + limit]}

    def auto_assign(self, assignees, max_open=5):
        if not isinstance(assignees, list) or not assignees:
            raise ValueError("assignees must be a nonempty array")
        names = []
        for element in assignees:
            if not isinstance(element, str) or not element.strip():
                raise ValueError("assignees elements must be nonblank strings")
            names.append(element.strip())
        if len(set(names)) != len(names):
            raise ValueError("assignees must not contain duplicate names")
        max_open = positive(max_open, "max_open")
        data = self._read()
        tickets = data.get("tickets", {})
        loads = {name: 0 for name in names}
        for ticket in tickets.values():
            if ticket["status"] == "open" and ticket.get("assignee") in loads:
                loads[ticket["assignee"]] += 1
        def candidate_key(ticket):
            opened_at = ticket.get("opened_at")
            return (PRIORITY_RANK[ticket.get("priority", "normal")],
                    opened_at is None, opened_at if opened_at is not None else 0,
                    ticket["ticket_id"])
        candidates = sorted((ticket for ticket in tickets.values()
                             if ticket["status"] == "open" and ticket.get("assignee") is None),
                            key=candidate_key)
        assigned = []
        assigned_ids = set()
        for ticket in candidates:
            available = [name for name in names if loads[name] < max_open]
            if not available:
                break
            chosen = min(available, key=lambda name: (loads[name], name))
            ticket["assignee"] = chosen
            loads[chosen] += 1
            assigned.append(ticket)
            assigned_ids.add(ticket["ticket_id"])
        if assigned:
            self._write(data)
        remaining = [ticket["ticket_id"] for ticket in candidates
                     if ticket["ticket_id"] not in assigned_ids]
        return {"assigned": assigned, "remaining": remaining}

    def route_assign(self, rules, max_open=5):
        if not isinstance(rules, list) or not rules:
            raise ValueError("rules must be a nonempty array")
        parsed = []
        categories = set()
        names = []
        for rule in rules:
            if not isinstance(rule, dict) or set(rule) != {"category", "assignees"}:
                raise ValueError("each rule must be an object with exactly category and assignees")
            category = rule["category"]
            if category is not None:
                category = text(category, "category")
            if category in categories:
                raise ValueError("rules must not contain duplicate categories")
            categories.add(category)
            assignees = rule["assignees"]
            if not isinstance(assignees, list) or not assignees:
                raise ValueError("assignees must be a nonempty array")
            rule_names = []
            for element in assignees:
                if not isinstance(element, str) or not element.strip():
                    raise ValueError("assignees elements must be nonblank strings")
                rule_names.append(element.strip())
            if len(set(rule_names)) != len(rule_names):
                raise ValueError("assignees must not contain duplicate names")
            parsed.append((category, rule_names))
            names.extend(rule_names)
        max_open = positive(max_open, "max_open")
        data = self._read()
        tickets = data.get("tickets", {})
        # Loads are shared across rules: one person's open tickets count against every rule.
        loads = {name: 0 for name in names}
        for ticket in tickets.values():
            if ticket["status"] == "open" and ticket.get("assignee") in loads:
                loads[ticket["assignee"]] += 1
        def candidate_key(ticket):
            opened_at = ticket.get("opened_at")
            return (PRIORITY_RANK[ticket.get("priority", "normal")],
                    opened_at is None, opened_at if opened_at is not None else 0,
                    ticket["ticket_id"])
        candidates = sorted((ticket for ticket in tickets.values()
                             if ticket["status"] == "open" and ticket.get("assignee") is None),
                            key=candidate_key)
        routes = dict(parsed)
        assigned = []
        assigned_ids = set()
        for ticket in candidates:
            rule_names = routes.get(ticket.get("category"))
            if rule_names is None:
                continue
            available = [name for name in rule_names if loads[name] < max_open]
            if not available:
                continue
            chosen = min(available, key=lambda name: (loads[name], name))
            ticket["assignee"] = chosen
            loads[chosen] += 1
            assigned.append(ticket)
            assigned_ids.add(ticket["ticket_id"])
        if assigned:
            self._write(data)
        remaining = [ticket["ticket_id"] for ticket in candidates
                     if ticket["ticket_id"] not in assigned_ids]
        return {"assigned": assigned, "remaining": remaining}

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

    def search_tickets(self, query, status=None, category=_UNSET, offset=0, limit=20):
        if not isinstance(query, str) or not query.strip():
            raise ValueError("query must be a nonempty string")
        if status not in (None, "open", "closed"):
            raise ValueError("status must be open or closed")
        if category is not _UNSET and category is not None:
            category = text(category, "category")
        # bool is a subclass of int, so compare types explicitly; floats and strings are rejected too.
        if type(offset) is not int or offset < 0:
            raise ValueError("offset must be a nonnegative integer")
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("limit must be an integer between 1 and 100")
        terms = [term.casefold() for term in query.strip().split()]
        matched = []
        for ticket in self._read().get("tickets", {}).values():
            if status is not None and ticket["status"] != status:
                continue
            if category is _UNSET:
                pass
            elif category is None:
                if ticket.get("category") is not None:
                    continue
            elif ticket.get("category") != category:
                continue
            fields = [ticket["customer"], ticket["subject"]]
            fields.extend(ticket.get("notes") or [])
            if ticket.get("resolution") is not None:
                fields.append(ticket["resolution"])
            response = ticket.get("first_response")
            if response is not None and response.get("message"):
                # Knowledge replies are searched by the saved message body, not the snapshot.
                fields.append(response["message"])
            for reply in ticket.get("replies") or []:
                if reply.get("message"):
                    fields.append(reply["message"])
            haystacks = [field.casefold() for field in fields]
            if all(any(term in haystack for haystack in haystacks) for term in terms):
                matched.append(ticket)
        matched.sort(key=lambda ticket: ticket["ticket_id"])
        return {"total": len(matched), "items": matched[offset:offset + limit]}
