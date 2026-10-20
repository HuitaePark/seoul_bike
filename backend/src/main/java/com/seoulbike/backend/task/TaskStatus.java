package com.seoulbike.backend.task;

public enum TaskStatus {
	QUEUED, RUNNING, WAITING_APPROVAL, DONE, FAILED;

	public boolean terminal() {
		return this == DONE || this == FAILED;
	}
}
