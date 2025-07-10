"""
CronJob Manager module for tracking scheduled cluster cleanup tasks.
"""

from kubernetes import client
from kubernetes.client.rest import ApiException
from datetime import datetime, timezone
from typing import List, Dict, Optional, Tuple
from colorama import Fore, Style


class CronJobManager:
    """Manages CronJob operations and monitoring."""
    
    def __init__(self, kubeconfig_path: Optional[str] = None):
        """
        Initialize the cronjob manager.
        
        Args:
            kubeconfig_path: Path to kubeconfig file. If None, uses default locations.
        """
        self.kubeconfig_path = kubeconfig_path
        self._load_config()
        
    def _load_config(self):
        """Load Kubernetes configuration."""
        try:
            if self.kubeconfig_path:
                from kubernetes import config
                config.load_kube_config(config_file=self.kubeconfig_path)
            else:
                from kubernetes import config
                config.load_kube_config()
        except Exception as e:
            raise Exception(f"Failed to load kubeconfig: {e}")
            
        # Initialize API clients
        self.batch_v1 = client.BatchV1Api()
        self.core_v1 = client.CoreV1Api()
        
    def get_nkp_cronjobs(self, namespace: str = "kommander") -> List[Dict]:
        """
        Get all CronJobs with the nkp-cluster-cleaner label.
        
        Args:
            namespace: Namespace to search for CronJobs
            
        Returns:
            List of CronJob objects with metadata
        """
        try:
            cronjobs = self.batch_v1.list_namespaced_cron_job(
                namespace=namespace,
                label_selector="app=nkp-cluster-cleaner"
            )
            
            cronjob_list = []
            for cj in cronjobs.items:
                # Calculate next scheduled time
                next_schedule = None
                if cj.status and cj.status.last_schedule_time:
                    # This is a simplified calculation - actual cron parsing would be more complex
                    next_schedule = "Calculated from cron expression"
                
                cronjob_info = {
                    "name": cj.metadata.name,
                    "namespace": cj.metadata.namespace,
                    "schedule": cj.spec.schedule,
                    "suspend": cj.spec.suspend or False,
                    "last_schedule_time": cj.status.last_schedule_time if cj.status else None,
                    "active_jobs": len(cj.status.active) if cj.status and cj.status.active else 0,
                    "successful_job_history_limit": cj.spec.successful_jobs_history_limit if cj.spec.successful_jobs_history_limit else 3,
                    "failed_job_history_limit": cj.spec.failed_jobs_history_limit if cj.spec.failed_jobs_history_limit else 1,
                    "creation_timestamp": cj.metadata.creation_timestamp,
                    "labels": cj.metadata.labels or {},
                    "annotations": cj.metadata.annotations or {}
                }
                cronjob_list.append(cronjob_info)
                
            return cronjob_list
            
        except ApiException as e:
            print(f"{Fore.RED}Failed to list CronJobs: {e}{Style.RESET_ALL}")
            return []
    
    def get_jobs_for_cronjob(self, cronjob_name: str, namespace: str = "kommander", limit: int = 10) -> List[Dict]:
        """
        Get recent Jobs created by a specific CronJob.
        
        Args:
            cronjob_name: Name of the CronJob
            namespace: Namespace to search
            limit: Maximum number of jobs to return
            
        Returns:
            List of Job objects with status information
        """
        try:
            # Get all jobs in the namespace
            jobs = self.batch_v1.list_namespaced_job(namespace=namespace)
            
            cronjob_jobs = []
            for job in jobs.items:
                # Check if this job was created by our cronjob
                if job.metadata.owner_references:
                    for owner in job.metadata.owner_references:
                        if (owner.kind == "CronJob" and 
                            owner.name == cronjob_name):
                            
                            job_info = {
                                "name": job.metadata.name,
                                "namespace": job.metadata.namespace,
                                "cronjob_name": cronjob_name,
                                "status": self._get_job_status(job),
                                "start_time": job.status.start_time if job.status else None,
                                "completion_time": job.status.completion_time if job.status else None,
                                "duration": self._calculate_duration(job),
                                "creation_timestamp": job.metadata.creation_timestamp,
                                "active_pods": job.status.active or 0 if job.status else 0,
                                "succeeded_pods": job.status.succeeded or 0 if job.status else 0,
                                "failed_pods": job.status.failed or 0 if job.status else 0,
                                "labels": job.metadata.labels or {}
                            }
                            cronjob_jobs.append(job_info)
                            break
            
            # Sort by creation time (newest first) and limit
            cronjob_jobs.sort(key=lambda x: x["creation_timestamp"], reverse=True)
            return cronjob_jobs[:limit]
            
        except ApiException as e:
            print(f"{Fore.RED}Failed to list Jobs for CronJob {cronjob_name}: {e}{Style.RESET_ALL}")
            return []
    
    def get_job_pods(self, job_name: str, namespace: str = "kommander") -> List[Dict]:
        """
        Get pods created by a specific Job.
        
        Args:
            job_name: Name of the Job
            namespace: Namespace to search
            
        Returns:
            List of Pod objects with status information
        """
        try:
            pods = self.core_v1.list_namespaced_pod(
                namespace=namespace,
                label_selector=f"job-name={job_name}"
            )
            
            pod_list = []
            for pod in pods.items:
                pod_info = {
                    "name": pod.metadata.name,
                    "namespace": pod.metadata.namespace,
                    "job_name": job_name,
                    "status": pod.status.phase,
                    "start_time": pod.status.start_time,
                    "creation_timestamp": pod.metadata.creation_timestamp,
                    "container_statuses": []
                }
                
                # Get container statuses
                if pod.status.container_statuses:
                    for container in pod.status.container_statuses:
                        container_info = {
                            "name": container.name,
                            "ready": container.ready,
                            "restart_count": container.restart_count,
                            "state": self._get_container_state(container.state)
                        }
                        pod_info["container_statuses"].append(container_info)
                
                pod_list.append(pod_info)
            
            return pod_list
            
        except ApiException as e:
            print(f"{Fore.RED}Failed to list Pods for Job {job_name}: {e}{Style.RESET_ALL}")
            return []
    
    def get_pod_logs(self, pod_name: str, container_name: str = None, namespace: str = "kommander", 
                     tail_lines: int = 100) -> str:
        """
        Get logs from a specific pod/container.
        
        Args:
            pod_name: Name of the pod
            container_name: Name of the container (optional for single-container pods)
            namespace: Namespace of the pod
            tail_lines: Number of recent log lines to retrieve
            
        Returns:
            Log content as string
        """
        try:
            logs = self.core_v1.read_namespaced_pod_log(
                name=pod_name,
                namespace=namespace,
                container=container_name,
                tail_lines=tail_lines,
                timestamps=True
            )
            return logs
        except ApiException as e:
            return f"Failed to retrieve logs: {e}"
    
    def _get_job_status(self, job) -> str:
        """
        Determine the overall status of a Job.
        
        Args:
            job: Kubernetes Job object
            
        Returns:
            Status string (Running, Succeeded, Failed, etc.)
        """
        if not job.status:
            return "Pending"
        
        succeeded = job.status.succeeded or 0
        failed = job.status.failed or 0
        active = job.status.active or 0
        
        if succeeded > 0:
            return "Succeeded"
        elif failed > 0:
            return "Failed"
        elif active > 0:
            return "Running"
        else:
            return "Pending"
    
    def _calculate_duration(self, job) -> Optional[str]:
        """
        Calculate the duration of a job.
        
        Args:
            job: Kubernetes Job object
            
        Returns:
            Duration string or None
        """
        if not job.status:
            return None
        
        start_time = job.status.start_time
        completion_time = job.status.completion_time
        
        if start_time and completion_time:
            duration = completion_time - start_time
            total_seconds = int(duration.total_seconds())
            
            if total_seconds < 60:
                return f"{total_seconds}s"
            elif total_seconds < 3600:
                return f"{total_seconds // 60}m {total_seconds % 60}s"
            else:
                hours = total_seconds // 3600
                minutes = (total_seconds % 3600) // 60
                return f"{hours}h {minutes}m"
        elif start_time:
            # Job is still running
            now = datetime.now(timezone.utc)
            duration = now - start_time.replace(tzinfo=timezone.utc)
            total_seconds = int(duration.total_seconds())
            
            if total_seconds < 60:
                return f"{total_seconds}s (running)"
            elif total_seconds < 3600:
                return f"{total_seconds // 60}m {total_seconds % 60}s (running)"
            else:
                hours = total_seconds // 3600
                minutes = (total_seconds % 3600) // 60
                return f"{hours}h {minutes}m (running)"
        
        return None
    
    def _get_container_state(self, state) -> str:
        """
        Get the current state of a container.
        
        Args:
            state: Container state object
            
        Returns:
            State string
        """
        if state.running:
            return "Running"
        elif state.waiting:
            return f"Waiting ({state.waiting.reason})"
        elif state.terminated:
            return f"Terminated ({state.terminated.reason})"
        else:
            return "Unknown"
    
    def get_all_scheduled_tasks_summary(self, namespace: str = "kommander") -> Dict:
        """
        Get a comprehensive summary of all scheduled tasks.
        
        Args:
            namespace: Namespace to search
            
        Returns:
            Dictionary with summary information
        """
        cronjobs = self.get_nkp_cronjobs(namespace)
        
        total_cronjobs = len(cronjobs)
        active_cronjobs = len([cj for cj in cronjobs if not cj["suspend"]])
        suspended_cronjobs = len([cj for cj in cronjobs if cj["suspend"]])
        
        # Get recent jobs for all cronjobs
        all_recent_jobs = []
        for cj in cronjobs:
            jobs = self.get_jobs_for_cronjob(cj["name"], namespace, limit=5)
            all_recent_jobs.extend(jobs)
        
        # Sort all jobs by creation time
        all_recent_jobs.sort(key=lambda x: x["creation_timestamp"], reverse=True)
        
        # Calculate success/failure rates
        completed_jobs = [job for job in all_recent_jobs if job["status"] in ["Succeeded", "Failed"]]
        successful_jobs = [job for job in completed_jobs if job["status"] == "Succeeded"]
        failed_jobs = [job for job in completed_jobs if job["status"] == "Failed"]
        running_jobs = [job for job in all_recent_jobs if job["status"] == "Running"]
        
        return {
            "total_cronjobs": total_cronjobs,
            "active_cronjobs": active_cronjobs,
            "suspended_cronjobs": suspended_cronjobs,
            "total_recent_jobs": len(all_recent_jobs),
            "successful_jobs": len(successful_jobs),
            "failed_jobs": len(failed_jobs),
            "running_jobs": len(running_jobs),
            "cronjobs": cronjobs,
            "recent_jobs": all_recent_jobs[:20]  # Limit to 20 most recent
        }