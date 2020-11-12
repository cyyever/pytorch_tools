from data_structure.cuda_task_queue import CUDATaskQueue


def test_cuda_task_queue():
    queue = CUDATaskQueue(
        lambda task, device: print(
            "hello world,device", device))
    queue.start()
    queue.add_task(())
    queue.stop()